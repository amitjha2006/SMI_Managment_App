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


@router.get("/purchase-invoices", response_model=list[PurchaseInvoiceSchema])
def list_purchase_invoices(db: Session = Depends(get_db)):
    return db.query(PurchaseInvoice).filter(PurchaseInvoice.is_archived == False).order_by(PurchaseInvoice.id.desc()).all()  # noqa: E712


@router.get("/purchase-invoices/{invoice_id}", response_model=PurchaseInvoiceSchema)
def get_purchase_invoice(invoice_id: int, db: Session = Depends(get_db)):
    inv = db.query(PurchaseInvoice).filter(PurchaseInvoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Purchase invoice not found")
    return inv


@router.post("/purchase-invoices", response_model=PurchaseInvoiceSchema)
def create_purchase_invoice(invoice: PurchaseInvoiceSchema, request: Request, db: Session = Depends(get_db)):
    supplier = db.query(Supplier).filter(Supplier.id == invoice.supplier_id).first()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")

    # Generate invoice number
    from backend.app.services.document_sequence import next_purchase_invoice_number
    invoice_number = next_purchase_invoice_number(db)

    inv = PurchaseInvoice(
        invoice_number=invoice_number,
        supplier_id=invoice.supplier_id,
        invoice_date=invoice.invoice_date,
        transport_charges=invoice.transport_charges or 0,
        other_charges=invoice.other_charges or 0,
        total_amount=invoice.total_amount or 0,
        status=invoice.status,
    )
    db.add(inv)
    db.flush()

    # Add items
    for item in invoice.items:
        db.add(PurchaseInvoiceItem(
            purchase_invoice_id=inv.id,
            raw_material_id=item.raw_material_id,
            quantity=item.quantity,
            rate=item.rate,
            amount=item.amount,
            transport_amount=item.transport_amount,
        ))

    _audit(db, request, "purchases", "create", record=inv, record_type="purchase_invoice", new_values={"invoice_number": invoice_number, "supplier_id": invoice.supplier_id})
    db.commit()
    db.refresh(inv)
    return inv


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