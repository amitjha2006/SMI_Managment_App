"""Purchase invoice and supplier payment routes"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session
from datetime import datetime
from decimal import Decimal

from backend.app.core.database import get_db
from backend.app.models import (
    PurchaseInvoice,
    PurchaseInvoiceItem,
    RawMaterial,
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


@router.get("/purchase-invoices")
def list_purchase_invoices(db: Session = Depends(get_db)):
    """List purchase invoices with supplier name/company for the invoices list."""
    rows = (
        db.query(PurchaseInvoice, Supplier)
        .join(Supplier, PurchaseInvoice.supplier_id == Supplier.id)
        .filter(PurchaseInvoice.is_archived == False)  # noqa: E712
        .order_by(PurchaseInvoice.id.desc())
        .all()
    )
    return [
        {
            "id": inv.id,
            "invoice_number": inv.invoice_number,
            "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
            "created_at": inv.created_at.isoformat() if inv.created_at else None,
            "supplier_id": inv.supplier_id,
            "supplier_name": sup.name,
            "supplier_company": sup.company_name or "",
            "total_amount": float(inv.total_amount or 0),
            "paid_amount": float(inv.paid_amount or 0),
            "outstanding_amount": float(inv.outstanding_amount or 0),
            "status": inv.payment_status,
            "is_archived": inv.is_archived,
        }
        for inv, sup in rows
    ]


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
                "product_id": it.product_id,
                "packaging_size_id": it.packaging_size_id,
                "quantity": float(it.quantity or 0),
                "weight": float(it.weight or 0),
                "rate_per_kg": float(it.rate_per_kg or 0),
                "amount": float(it.amount or 0),
                "taxable_value": float(it.taxable_value or 0),
                "gst_rate": float(it.gst_rate or 0),
                "cgst": float(it.cgst or 0),
                "sgst": float(it.sgst or 0),
                "igst": float(it.igst or 0),
                "product_name": it.product_name or "",
                "size_name": it.size_name or "",
                "hsn_code": it.hsn_code or "",
                "sequence": it.sequence,
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
        transport = invoice.get("transport_charges") or 0
        other = invoice.get("other_charges") or 0
        total = invoice.get("total_amount") or 0
        status = invoice.get("status")
        items = invoice.get("items") or []
    else:
        inv_date = invoice.invoice_date
        transport = invoice.transport_charges or 0
        other = invoice.other_charges or 0
        total = invoice.total_amount or 0
        status = invoice.status
        items = invoice.items or []

    inv = PurchaseInvoice(
        invoice_number=invoice_number,
        supplier_id=gid,
        invoice_date=inv_date,
        transport_charges=transport,
        other_charges=other,
        total_amount=total,
        payment_status=status,
    )
    db.add(inv)
    db.flush()

    # Add items
    for item in items:
        if isinstance(item, dict):
            db.add(PurchaseInvoiceItem(
                purchase_invoice_id=inv.id,
                raw_material_id=item.get("raw_material_id"),
                quantity=item.get("quantity") or 0,
                rate=item.get("rate") or 0,
                amount=item.get("amount") or 0,
                transport_amount=item.get("transport_amount") or 0,
                unit=item.get("unit") or "kg",
            ))
        else:
            db.add(PurchaseInvoiceItem(
                purchase_invoice_id=inv.id,
                raw_material_id=item.raw_material_id,
                quantity=item.quantity,
                rate=item.rate,
                amount=item.amount,
                transport_amount=item.transport_amount,
            ))

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
                "transport_amount": float(it.transport_amount or 0),
            }
            for it in (inv.items or [])
        ],
    }


@router.delete("/purchase-invoices/{invoice_id}")
def archive_purchase_invoice(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    inv = db.query(PurchaseInvoice).filter(PurchaseInvoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Purchase invoice not found")
    reason = _change_reason(request)
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
    reason = _change_reason(request)
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