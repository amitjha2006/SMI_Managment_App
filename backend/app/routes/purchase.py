"""Purchase invoice and supplier payment routes"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session
from datetime import datetime
from decimal import Decimal

D = Decimal


class _InvoiceRow(dict):
    """dict subclass that also exposes keys as attributes."""
    def __init__(self, **kwargs):
        super().__init__(kwargs)

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

from backend.app.core.database import get_db
from backend.app.models import (
    PurchaseInvoice,
    PurchaseInvoiceItem,
    RawMaterial,
    RawMaterialStockMovement,
    Supplier,
    SupplierPayment,
)
from backend.app.schemas import PurchaseInvoiceSchema, SupplierPaymentSchema
from backend.app.core.shared import (
    _audit,
    _change_reason,
    _reject_if_archived,
    _archive_record,
    _restore_record,
    _current_user,
    _ist_wall,
    _request_user_and_role,
)

router = APIRouter()


def _post_purchase_movement(db, inv, rm_id, qty, unit, rate):
    """Post one purchase movement and advance on-hand stock (confirmed only).

    Mirrors the raw-material summary rule: only confirmed, non-archived
    purchases count toward stock. Kept as the single purchase -> stock writer
    so current_stock and the movement ledger stay consistent (purchase -
    consumption +- adjustment).
    """
    if not inv.is_confirmed:
        return
    rm = db.query(RawMaterial).filter(RawMaterial.id == rm_id).first()
    if not rm:
        return
    rm.current_stock = (rm.current_stock or D("0")) + qty
    db.add(RawMaterialStockMovement(
        raw_material_id=rm_id,
        movement_type="purchase",
        quantity=qty,
        unit=unit,
        rate=rate,
        reference_type="purchase_invoice",
        reference_id=inv.id,
        notes=f"Purchase {inv.invoice_number}",
        movement_date=inv.invoice_date,
    ))


@router.get("/purchase-invoices")
def list_purchase_invoices(db: Session = Depends(get_db)):
    """List purchase invoices with supplier name/company for the invoices list."""
    rows = (
        db.query(PurchaseInvoice, Supplier)
        .outerjoin(Supplier, PurchaseInvoice.supplier_id == Supplier.id)
        .filter(PurchaseInvoice.is_archived == False)  # noqa: E712
        .order_by(PurchaseInvoice.id.desc())
        .all()
    )
    result = []
    for inv, sup in rows:
        result.append(_InvoiceRow(
            id=inv.id,
            invoice_number=inv.invoice_number,
            invoice_date=inv.invoice_date.isoformat() if inv.invoice_date else None,
            supplier_id=inv.supplier_id,
            supplier_name=sup.name if sup else "",
            supplier_company=sup.company_name if sup else "",
            total_amount=float(inv.total_amount or 0),
            paid_amount=float(inv.paid_amount or 0),
            outstanding_amount=float(inv.outstanding_amount or 0),
            status=inv.payment_status.value if inv.payment_status else "",
            items=[
                {
                    "id": it.id,
                    "raw_material_id": it.raw_material_id,
                    "material_name": it.raw_material.name if it.raw_material else "",
                    "quantity": float(it.quantity or 0),
                    "unit": it.unit or "",
                    "rate": float(it.rate or 0),
                    "amount": float(it.amount or 0),
                }
                for it in (inv.items or [])
            ],
        ))
    return result


@router.get("/purchase-invoices/{invoice_id}")
def get_purchase_invoice(invoice_id: int, db: Session = Depends(get_db)):
    row = (
        db.query(PurchaseInvoice, Supplier)
        .join(Supplier, PurchaseInvoice.supplier_id == Supplier.id)
        .filter(PurchaseInvoice.id == invoice_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Purchase invoice not found")
    inv, sup = row
    return {
        "id": inv.id,
        "invoice_number": inv.invoice_number,
        "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
        "created_at": inv.created_at.isoformat() if inv.created_at else None,
        "supplier_id": inv.supplier_id,
        "supplier_name": sup.name,
        "supplier_company": sup.company_name or "",
        "gst_percent": float(inv.gst_percent or 0),
        "other_charges": float(inv.other_charges or 0),
        "transport_charges": float(inv.transport_charges or 0),
        "discount": float(inv.discount or 0),
        "total_amount": float(inv.total_amount or 0),
        "paid_amount": float(inv.paid_amount or 0),
        "outstanding_amount": float(inv.outstanding_amount or 0),
        "notes": inv.notes,
        "status": inv.payment_status.value if inv.payment_status else "",
        "is_archived": inv.is_archived,
        "archived_at": inv.archived_at.isoformat() if inv.archived_at else None,
        "archived_by": inv.archived_by,
        "items": [
            {
                "id": it.id,
                "purchase_invoice_id": it.purchase_invoice_id,
                "raw_material_id": it.raw_material_id,
                "material_name": it.raw_material.name if it.raw_material else "",
                "quantity": float(it.quantity or 0),
                "unit": it.unit or "",
                "rate": float(it.rate or 0),
                "amount": float(it.amount or 0),
                "gst_percent": float(it.gst_percent or 0),
                "cgst_amount": float(it.cgst_amount or 0),
                "sgst_amount": float(it.sgst_amount or 0),
                "igst_amount": float(it.igst_amount or 0),
                "transport_amount": float(it.transport_amount or 0),
                "other_amount": float(it.other_amount or 0),
                "total_cost_per_kg": float(
                    (it.rate or 0)
                    + ((it.transport_amount or 0) / (it.quantity or 1))
                    + ((it.other_amount or 0) / (it.quantity or 1))
                ),
            }
            for it in (inv.items or [])
        ],
    }


@router.post("/purchase-invoices", response_model=PurchaseInvoiceSchema)
def create_purchase_invoice(invoice: PurchaseInvoiceSchema, request: Request, db: Session = Depends(get_db)):
    # Accept both schema objects and plain dicts (tests call directly with dicts)
    if isinstance(invoice, dict):
        gid = invoice.get("supplier_id")
    else:
        gid = invoice.supplier_id
    supplier = db.query(Supplier).filter(Supplier.id == gid).first()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")

    # Generate invoice number
    from backend.app.services.document_sequence import next_purchase_invoice_number
    invoice_number = next_purchase_invoice_number(db)

    if isinstance(invoice, dict):
        inv_date = invoice.get("invoice_date")
        if isinstance(inv_date, str):
            inv_date = datetime.fromisoformat(inv_date)
        transport = D(str(invoice.get("transport_charges") or 0))
        other = D(str(invoice.get("other_charges") or 0))
        total = invoice.get("total_amount")
        total = D(str(total)) if total is not None else None
        status = invoice.get("status")
        items = invoice.get("items") or []
        paid = D(str(invoice.get("paid_amount") or 0))
    else:
        inv_date = invoice.invoice_date
        transport = D(str(invoice.transport_charges or 0))
        other = D(str(invoice.other_charges or 0))
        total = D(str(invoice.total_amount)) if invoice.total_amount else None
        status = invoice.status
        items = invoice.items or []
        paid = D(str(invoice.paid_amount or 0))

    if transport < 0 or other < 0:
        raise HTTPException(status_code=400, detail="Charges cannot be negative")

    inv = PurchaseInvoice(
        invoice_number=invoice_number,
        supplier_id=gid,
        invoice_date=inv_date,
        transport_charges=transport,
        other_charges=other,
        total_amount=total if total is not None else D("0"),
        payment_status=status,
        paid_amount=paid,
    )
    db.add(inv)
    db.flush()

    # Add items: GST-inclusive amount, pro-rata transport/other allocation.
    total_kg = sum(float(it.get("quantity") or 0) for it in items)
    tp_kg = (transport / D(str(total_kg))) if total_kg else D("0")
    op_kg = (other / D(str(total_kg))) if total_kg else D("0")

    goods_value = D("0")
    for item in items:
        if isinstance(item, dict):
            rm_id = item.get("raw_material_id")
            qty = D(str(item.get("quantity") or 0))
            rate = D(str(item.get("rate") or 0))
            gst = D(str(item.get("gst_percent") or 0))
            amt = item.get("amount")
            if amt is None:
                amt = (qty * rate * (D("1") + gst / D("100"))).quantize(D("0.01"))
            else:
                amt = D(str(amt))
            unit = item.get("unit") or "kg"
            t_amt = (qty * tp_kg).quantize(D("0.01"))
            o_amt = (qty * op_kg).quantize(D("0.01"))
            goods_value += amt
            db.add(PurchaseInvoiceItem(
                purchase_invoice_id=inv.id,
                raw_material_id=rm_id,
                quantity=qty,
                unit=unit,
                rate=rate,
                gst_percent=gst,
                amount=amt,
                transport_amount=t_amt,
                other_amount=o_amt,
            ))
            _post_purchase_movement(db, inv, rm_id, qty, unit, rate)
        else:
            amt = D(str(item.amount or 0))
            goods_value += amt
            db.add(PurchaseInvoiceItem(
                purchase_invoice_id=inv.id,
                raw_material_id=item.raw_material_id,
                quantity=item.quantity,
                unit=item.unit,
                rate=item.rate,
                gst_percent=item.gst_percent,
                amount=amt,
                transport_amount=D(str(item.transport_amount or 0)),
                other_amount=D(str(item.other_amount or 0)),
            ))
            _post_purchase_movement(
                db, inv, item.raw_material_id, item.quantity, item.unit, item.rate
            )

    # Payable total excludes charges; follows the paper invoice when typed.
    if total is None:
        total = goods_value
    inv.total_amount = total
    inv.outstanding_amount = max(D("0"), total - paid)

    _audit(db, request, "purchases", "create", record=inv, record_type="purchase_invoice", new_values={"invoice_number": invoice_number, "supplier_id": gid})
    db.commit()
    db.refresh(inv)
    return {
        "id": inv.id,
        "invoice_number": inv.invoice_number,
        "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
        "created_at": inv.created_at.isoformat() if inv.created_at else None,
        "supplier_id": inv.supplier_id,
        "supplier_name": supplier.name,
        "supplier_company": supplier.company_name or "",
        "gst_percent": float(inv.gst_percent or 0),
        "other_charges": float(inv.other_charges or 0),
        "transport_charges": float(inv.transport_charges or 0),
        "discount": float(inv.discount or 0),
        "total_amount": float(inv.total_amount or 0),
        "paid_amount": float(inv.paid_amount or 0),
        "outstanding_amount": float(inv.outstanding_amount or 0),
        "notes": inv.notes,
        "status": inv.payment_status.value if inv.payment_status else "",
        "is_archived": inv.is_archived,
        "archived_at": inv.archived_at.isoformat() if inv.archived_at else None,
        "archived_by": inv.archived_by,
        "items": [
            {
                "id": it.id,
                "purchase_invoice_id": it.purchase_invoice_id,
                "raw_material_id": it.raw_material_id,
                "quantity": float(it.quantity or 0),
                "unit": it.unit or "",
                "rate": float(it.rate or 0),
                "amount": float(it.amount or 0),
                "gst_percent": float(it.gst_percent or 0),
                "transport_amount": float(it.transport_amount or 0),
                "other_amount": float(it.other_amount or 0),
                "total_cost_per_kg": float(
                    (D(str(it.rate or 0)) * (D("1") + D(str(it.gst_percent or 0)) / D("100")))
                    + D(str(it.transport_amount or 0)) / D(str(it.quantity or 1))
                    + D(str(it.other_amount or 0)) / D(str(it.quantity or 1))
                ),
            }
            for it in (inv.items or [])
        ],
    }


@router.delete("/purchase-invoices/{invoice_id}")
def archive_purchase_invoice(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    inv = db.query(PurchaseInvoice).filter(PurchaseInvoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Purchase invoice not found")
    reason = ""
    if not _archive_record(db, request, inv, "purchases", "purchase_invoice"):
        raise HTTPException(status_code=409, detail="Purchase invoice already archived")
    _audit(db, request, "purchases", "archive", record=inv, record_type="purchase_invoice", old_values={"is_archived": False}, new_values={"is_archived": True}, notes=reason)
    db.commit()
    return {"status": "archived", "id": invoice_id}


@router.post("/purchase-invoices/{invoice_id}/restore")
def restore_purchase_invoice(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    inv = db.query(PurchaseInvoice).filter(PurchaseInvoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Purchase invoice not found")
    reason = ""
    if not _restore_record(db, request, inv, "purchases", "purchase_invoice"):
        raise HTTPException(status_code=409, detail="Purchase invoice is not archived")
    _audit(db, request, "purchases", "restore", record=inv, record_type="purchase_invoice", old_values={"is_archived": True}, new_values={"is_archived": False}, notes=reason)
    db.commit()
    return {"status": "restored", "id": invoice_id}


# Supplier payments
@router.get("/supplier-payments/invoice/{invoice_id}", response_model=list[SupplierPaymentSchema])
def list_supplier_payments(invoice_id: int, db: Session = Depends(get_db)):
    return db.query(SupplierPayment).filter(SupplierPayment.purchase_invoice_id == invoice_id).order_by(SupplierPayment.id.desc()).all()


@router.post("/supplier-payments", response_model=SupplierPaymentSchema)
def create_supplier_payment(payment: SupplierPaymentSchema, request: Request, db: Session = Depends(get_db)):
    inv = db.query(PurchaseInvoice).filter(PurchaseInvoice.id == payment.purchase_invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Purchase invoice not found")

    pay = SupplierPayment(
        purchase_invoice_id=payment.purchase_invoice_id,
        payment_date=payment.payment_date,
        amount=payment.amount,
        payment_mode=payment.payment_mode,
        reference=payment.reference,
    )
    db.add(pay)
    _audit(db, request, "purchases", "payment_create", record=pay, record_type="supplier_payment", new_values={"invoice_id": payment.purchase_invoice_id, "amount": float(payment.amount or 0)})
    db.commit()
    db.refresh(pay)
    return pay


@router.delete("/supplier-payments/{payment_id}")
def archive_supplier_payment(payment_id: int, request: Request, db: Session = Depends(get_db)):
    pay = db.query(SupplierPayment).filter(SupplierPayment.id == payment_id).first()
    if not pay:
        raise HTTPException(status_code=404, detail="Payment not found")
    reason = _change_reason(request)
    if not _archive_record(db, request, pay, "purchases", "supplier_payment"):
        raise HTTPException(status_code=409, detail="Payment already archived")
    _audit(db, request, "purchases", "payment_archive", record=pay, record_type="supplier_payment", old_values={"is_archived": False}, new_values={"is_archived": True}, notes=reason)
    db.commit()
    return {"status": "archived", "id": payment_id}


@router.post("/supplier-payments/{payment_id}/restore")
def restore_supplier_payment(payment_id: int, request: Request, db: Session = Depends(get_db)):
    pay = db.query(SupplierPayment).filter(SupplierPayment.id == payment_id).first()
    if not pay:
        raise HTTPException(status_code=404, detail="Payment not found")
    reason = _change_reason(request)
    if not _restore_record(db, request, pay, "purchases", "supplier_payment"):
        raise HTTPException(status_code=409, detail="Payment is not archived")
    _audit(db, request, "purchases", "payment_restore", record=pay, record_type="supplier_payment", old_values={"is_archived": True}, new_values={"is_archived": False}, notes=reason)
    db.commit()
    return {"status": "restored", "id": payment_id}