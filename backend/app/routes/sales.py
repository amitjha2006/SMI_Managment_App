"""Sales challan, finished stock, stock adjustments, analysis routes"""
import re
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, false
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, date
from decimal import Decimal as D

from backend.app.core.database import get_db
from backend.app.models import (
    Customer,
    CustomerPayment,
    Expense,
    ExpenseCategory,
    FinishedProductStockMovement,
    PackagingSize,
    Product,
    ProductionEntry,
    PurchaseInvoice,
    RawMaterial,
    RawMaterialStockMovement,
    SalesChallan,
    SalesChallanItem,
    SalesInvoice,
    SalesInvoiceItem,
    Supplier,
    SupplierPayment,
)
from backend.app.models.finished_product import FinishedProductMovementType
from backend.app.schemas import SalesChallanSchema, ChallanStatus, SalesInvoiceSchema, CustomerPaymentSchema
from backend.app.core.shared import (
    _audit,
    _change_reason,
    _current_user,
    _reject_if_archived,
    _archive_record,
    _restore_record,
    _ist_wall,
    _request_payload,
    _request_user_and_role,
)
from backend.app.services import challan_snapshot as _snap
from backend.app.services.pdf_export import render_invoice_pdf
from backend.app.services.stock_posting import challan_stock_posted, post_challan_sales_movements

router = APIRouter()


@router.get("/challans/dispatch-options")
def challan_dispatch_options(db: Session = Depends(get_db)):
    """Distinct transporters and vehicles from recent challans for datalists.

    Most-recent-first, case-insensitive dedup (latest casing wins), blanks
    and nulls dropped. Frontend fills <datalist> options from these.
    """
    rows = (
        db.query(SalesChallan.transporter, SalesChallan.vehicle_no, SalesChallan.challan_date)
        .filter(
            SalesChallan.is_archived == False,  # noqa: E712
            SalesChallan.challan_date.isnot(None),
        )
        .order_by(SalesChallan.challan_date.desc(), SalesChallan.id.desc())
        .all()
    )
    transporters, vehicles = [], []
    seen_t, seen_v = set(), set()
    for transporter, vehicle, _date in rows:
        if transporter and isinstance(transporter, str):
            key = transporter.strip().lower()
            if key and key not in seen_t:
                seen_t.add(key)
                transporters.append(transporter.strip())
        if vehicle and isinstance(vehicle, str):
            key = vehicle.strip().lower()
            if key and key not in seen_v:
                seen_v.add(key)
                vehicles.append(vehicle.strip())
    return {"transporters": transporters, "vehicles": vehicles}


# Challan routes
class _ChallanRow(dict):
    """dict subclass that also exposes keys as attributes, so both
    r["challan_number"] and r.challan_number work."""
    def __init__(self, **kwargs):
        super().__init__(kwargs)

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")


@router.get("/challans", response_model=list[SalesChallanSchema])
def list_challans(request: Request = None, db: Session = Depends(get_db)):
    from backend.app.core.shared import _challan_entry_allowed, _current_user, _latest_login_at
    user = _current_user(db, request) if request else None
    query = db.query(SalesChallan).filter(SalesChallan.is_archived == False).order_by(SalesChallan.id.desc())  # noqa: E712
    if user and hasattr(user, 'role'):
        from backend.app.models.user import UserRole
        if user.role == UserRole.CHALLAN_ENTRY:
            # Get latest login for this user
            login_at = _latest_login_at(db, user.id)
            if login_at:
                from datetime import datetime
                query = query.filter(SalesChallan.challan_date >= login_at.date())
    return [
        _ChallanRow(
            id=c.id,
            challan_number=c.challan_number,
            challan_date=c.challan_date.isoformat() if c.challan_date else None,
            customer_id=c.customer_id,
            customer_name=c.customer.name if c.customer else "",
            status=c.status.value if c.status else None,
            total_price=float(c.total_price or 0),
            paid_amount=float(c.paid_amount or 0),
            outstanding_amount=float(c.outstanding_amount or 0),
            total_weight=float(c.total_weight or 0),
            dispatched_at=c.dispatched_at.isoformat() if c.dispatched_at else None,
            delivered_at=c.delivered_at.isoformat() if c.delivered_at else None,
            items=[],
            created_at=c.created_at.isoformat() if c.created_at else None,
        )
        for c in query.all()
    ]


@router.get("/challans/{challan_id}")
def get_challan(challan_id: int, db: Session = Depends(get_db)):
    row = (
        db.query(SalesChallan, Customer)
        .join(Customer, SalesChallan.customer_id == Customer.id)
        .filter(SalesChallan.id == challan_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Challan not found")
    c, cust = row
    return {
        "id": c.id,
        "challan_number": c.challan_number,
        "challan_date": c.challan_date.isoformat() if c.challan_date else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "customer_id": c.customer_id,
        "customer_name": cust.name,
        "status": c.status.value if c.status else "",
        "total_weight": float(c.total_weight or 0),
        "total_price": float(c.total_price or 0),
        "paid_amount": float(c.paid_amount or 0),
        "outstanding_amount": float(c.outstanding_amount or 0),
        "notes": c.notes,
        "dispatched_at": c.dispatched_at.isoformat() if c.dispatched_at else None,
        "delivered_at": c.delivered_at.isoformat() if c.delivered_at else None,
        "transporter": c.transporter or "",
        "vehicle_no": c.vehicle_no or "",
        "company_name": c.company_name or "",
        "company_address": c.company_address or "",
        "company_gstin": c.company_gstin or "",
        "company_phone": c.company_phone or "",
        "company_email": c.company_email or "",
        "company_terms": c.company_terms or "",
        "cust_company_name": c.cust_company_name or "",
        "cust_address": c.cust_address or "",
        "cust_gst_no": c.cust_gst_no or "",
        "cust_contact_number": c.cust_contact_number or "",
        "company_snapshot_at": c.company_snapshot_at.isoformat() if c.company_snapshot_at else None,
        "footer_id": c.footer_id,
        "is_archived": c.is_archived,
        "archived_at": c.archived_at.isoformat() if c.archived_at else None,
        "archived_by": c.archived_by,
        "items": [
            {
                "id": it.id,
                "challan_id": it.challan_id,
                "product_id": it.product_id,
                "packaging_size_id": it.packaging_size_id,
                "quantity": float(it.quantity or 0),
                "weight": float(it.weight or 0),
                "rate_per_kg": float(it.rate_per_kg or 0),
                "amount": float(it.amount or 0),
                "product_name": it.product_name or "",
                "size_name": it.size_name or "",
            }
            for it in (c.items or [])
        ],
    }


@router.get("/challans/{challan_id}/invoice")
def get_challan_invoice(challan_id: int, db: Session = Depends(get_db)):
    """Get the sales invoice for a challan, if one exists."""
    inv = db.query(SalesInvoice).filter(SalesInvoice.challan_id == challan_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return {
        "id": inv.id,
        "invoice_number": inv.invoice_number,
        "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
        "challan_id": inv.challan_id,
        "customer_id": inv.customer_id,
        "grand_total": float(inv.grand_total or 0),
    }


@router.post("/challans/{challan_id}/invoice")
def create_invoice_from_challan(challan_id: int, request: Request, db: Session = Depends(get_db)):
    """Create a sales invoice from a confirmed challan."""
    ch = db.query(SalesChallan).filter(SalesChallan.id == challan_id).first()
    if not ch:
        raise HTTPException(status_code=404, detail="Challan not found")
    if str(ch.status).lower() == "draft":
        raise HTTPException(status_code=400, detail="Challan must be confirmed first")

    # Check if invoice already exists
    existing = db.query(SalesInvoice).filter(SalesInvoice.challan_id == challan_id).first()
    if existing:
        raise HTTPException(status_code=409, detail="Invoice already exists for this challan")

    # Generate invoice number
    from backend.app.services.document_sequence import next_invoice_number
    invoice_number = next_invoice_number(db)

    # Create invoice
    inv = SalesInvoice(
        invoice_number=invoice_number,
        invoice_date=_ist_wall(),
        challan_id=ch.id,
        customer_id=ch.customer_id,
        intra_state=True,
        total_quantity=ch.total_weight,
        total_weight=ch.total_weight,
        taxable_value=ch.total_price,
        total_tax=D("0"),
        cgst=D("0"),
        sgst=D("0"),
        igst=D("0"),
        grand_total=ch.total_price,
    )
    db.add(inv)
    db.flush()

    # Copy challan items to invoice items
    for item in ch.items:
        db.add(SalesInvoiceItem(
            invoice_id=inv.id,
            product_id=item.product_id,
            packaging_size_id=item.packaging_size_id,
            quantity=item.quantity,
            weight=item.weight,
            rate_per_kg=item.rate_per_kg,
            amount=item.amount,
        ))

    _audit(db, request, "sales", "invoice_create", record=inv, record_type="sales_invoice",
           new_values={"invoice_number": invoice_number, "challan_id": ch.id})
    db.commit()
    db.refresh(inv)
    return {
        "id": inv.id,
        "invoice_number": inv.invoice_number,
        "challan_id": inv.challan_id,
        "grand_total": float(inv.grand_total or 0),
    }


@router.get("/payments/challan/{challan_id}")
def list_challan_payments(challan_id: int, db: Session = Depends(get_db)):
    """List customer payments for a challan."""
    payments = (
        db.query(CustomerPayment)
        .filter(CustomerPayment.challan_id == challan_id)
        .order_by(CustomerPayment.id.desc())
        .all()
    )
    return [
        {
            "id": p.id,
            "challan_id": p.challan_id,
            "amount": float(p.amount or 0),
            "payment_mode": p.payment_mode or "",
            "transaction_ref": p.transaction_ref or "",
            "payment_date": p.payment_date.isoformat() if p.payment_date else None,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in payments
    ]


def _challan_payload(payload) -> dict:
    """Accept both a Pydantic payload and a plain dict from callers/tests."""
    if payload is None:
        return {}
    return payload if isinstance(payload, dict) else payload.dict(exclude_unset=True)


def _challan_totals(ch):
    """Recompute header totals from the item rows."""
    total_weight = _ch_dec(0)
    total_price = _ch_dec(0)
    for it in ch.items:
        total_weight += _ch_dec(it.weight)
        total_price += _ch_dec(it.amount)
    ch.total_weight = total_weight
    ch.total_price = total_price


def _ch_dec(value):
    from decimal import Decimal
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _ch_dec(value):
    from decimal import Decimal
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


@router.post("/challans", response_model=SalesChallanSchema)
def create_challan(challan, request: Request, db: Session = Depends(get_db)):
    ch = _challan_payload(challan)
    customer = db.query(Customer).filter(Customer.id == ch.get("customer_id")).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    from backend.app.services.document_sequence import next_challan_number
    manual_number = (ch.get("challan_number") or "").strip()
    if manual_number:
        existing = db.query(SalesChallan).filter(
            func.lower(SalesChallan.challan_number) == manual_number.lower()
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="Challan number already exists")
        challan_number = manual_number
    else:
        challan_number = next_challan_number(db)

    challan_date = ch.get("challan_date")
    if isinstance(challan_date, str):
        from datetime import datetime
        challan_date = datetime.fromisoformat(challan_date)

    items = ch.get("items") or []
    total_weight = _ch_dec(0)
    total_price = _ch_dec(0)
    rows = []
    for item in items:
        qty = _ch_dec(item.get("quantity"))
        weight = _ch_dec(item.get("weight"))
        if not weight:
            size = db.query(PackagingSize).filter(PackagingSize.id == item.get("packaging_size_id")).first()
            if not size:
                raise HTTPException(status_code=400, detail="Packaging size not found")
            if not size.is_active:
                raise HTTPException(status_code=400, detail="Packaging size is not active")
            if size and size.weight_in_kg:
                weight = qty * _ch_dec(size.weight_in_kg)
        rate = _ch_dec(item.get("rate_per_kg", item.get("rate", 0)))
        amount = _ch_dec(item.get("amount"))
        if not amount:
            amount = weight * rate
        total_weight += weight
        total_price += amount
        rows.append({
            "product_id": item.get("product_id"),
            "packaging_size_id": item.get("packaging_size_id"),
            "quantity": qty,
            "weight": weight,
            "rate_per_kg": rate,
            "amount": amount,
        })

    c = SalesChallan(
        challan_number=challan_number,
        customer_id=ch.get("customer_id"),
        challan_date=challan_date,
        status=ChallanStatus.DRAFT,
        total_price=total_price,
        total_weight=total_weight,
        notes=ch.get("notes"),
    )
    db.add(c)
    db.flush()
    for r in rows:
        db.add(SalesChallanItem(challan_id=c.id, **r))

    _audit(db, request, "sales", "create", record=c, record_type="sales_challan", new_values={"challan_number": challan_number, "customer_id": c.customer_id})
    db.commit()
    db.refresh(c)
    return _challan_dict(c)


def _challan_dict(c):
    return {
        "id": c.id,
        "challan_number": c.challan_number,
        "challan_date": c.challan_date,
        "customer_id": c.customer_id,
        "status": c.status.value if c.status else None,
        "total_weight": c.total_weight,
        "total_price": c.total_price,
        "paid_amount": c.paid_amount,
        "outstanding_amount": c.outstanding_amount,
        "notes": c.notes,
        "dispatched_at": c.dispatched_at,
        "items": c.items or [],
    }


@router.put("/challans/{challan_id}", response_model=SalesChallanSchema)
def update_challan(challan_id: int, challan, request: Request, db: Session = Depends(get_db)):
    c = db.query(SalesChallan).filter(SalesChallan.id == challan_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Challan not found")
    _reject_if_archived(c, "challan")
    if c.status != ChallanStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Only DRAFT challans can be edited")
    reason = ""

    data = _challan_payload(challan)
    for field, value in data.items():
        if value is not None and field != "items" and hasattr(c, field):
            setattr(c, field, value)

    # Update items if provided
    if data.get("items") is not None:
        existing_items = db.query(SalesChallanItem).filter(SalesChallanItem.challan_id == challan_id).all()
        for item in existing_items:
            db.delete(item)
        for item in data["items"]:
            qty = _ch_dec(item.get("quantity"))
            weight = _ch_dec(item.get("weight"))
            rate = _ch_dec(item.get("rate_per_kg", item.get("rate", 0)))
            amount = _ch_dec(item.get("amount")) or weight * rate
            db.add(SalesChallanItem(
                challan_id=c.id,
                product_id=item.get("product_id"),
                packaging_size_id=item.get("packaging_size_id"),
                quantity=qty,
                weight=weight,
                rate_per_kg=rate,
                amount=amount,
            ))
        db.flush()
        _challan_totals(c)

    _audit(db, request, "sales", "update", record=c, record_type="sales_challan", new_values={"challan_number": c.challan_number}, notes=reason)
    db.commit()
    db.refresh(c)
    return _challan_dict(c)


def _draft_gate(c):
    """Draft-only edits: refuse once confirmed or dispatched."""
    if c.status != ChallanStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Only DRAFT challans can be edited")
    if c.dispatched_at:
        raise HTTPException(status_code=409, detail="Dispatched challans cannot be edited")


@router.put("/challans/{challan_id}/draft", response_model=SalesChallanSchema)
def update_draft_challan(challan_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    """Header-only edit of a draft challan. Buyer snapshot follows the customer."""
    c = db.query(SalesChallan).filter(SalesChallan.id == challan_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Challan not found")
    _reject_if_archived(c, "challan")
    _draft_gate(c)

    data = payload or {}
    if "customer_id" in data and data["customer_id"] != c.customer_id:
        customer = db.query(Customer).filter(Customer.id == data["customer_id"]).first()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        c.customer_id = customer.id
        from backend.app.services import challan_snapshot as snap
        snap.apply_snapshot(c, snap.company_profile(db), customer)
    if "challan_date" in data and data["challan_date"]:
        from datetime import datetime
        c.challan_date = (
            datetime.fromisoformat(data["challan_date"])
            if isinstance(data["challan_date"], str) else data["challan_date"]
        )
    if "notes" in data:
        c.notes = data["notes"]

    _audit(db, request, "sales", "draft_header_update", record=c, record_type="sales_challan",
           new_values={"customer_id": c.customer_id, "notes": c.notes})
    db.commit()
    db.refresh(c)
    return _challan_dict(c)


@router.put("/challans/{challan_id}/items/{item_id}/draft")
def update_draft_challan_item(challan_id: int, item_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    """Qty/rate edit on one draft line; header totals recomputed."""
    c = db.query(SalesChallan).filter(SalesChallan.id == challan_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Challan not found")
    _reject_if_archived(c, "challan")
    _draft_gate(c)
    row = db.query(SalesChallanItem).filter(
        SalesChallanItem.id == item_id, SalesChallanItem.challan_id == challan_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Challan item not found")

    data = payload or {}
    if "quantity" in data and data["quantity"] is not None:
        row.quantity = _ch_dec(data["quantity"])
        size = db.query(PackagingSize).filter(PackagingSize.id == row.packaging_size_id).first()
        if size and size.weight_in_kg:
            row.weight = row.quantity * _ch_dec(size.weight_in_kg)
    if "rate_per_kg" in data and data["rate_per_kg"] is not None:
        row.rate_per_kg = _ch_dec(data["rate_per_kg"])
    row.amount = _ch_dec(row.weight) * _ch_dec(row.rate_per_kg)

    _challan_totals(c)
    _audit(db, request, "sales", "draft_line_update", record=c, record_type="sales_challan",
           new_values={"item_id": item_id, "quantity": row.quantity, "rate_per_kg": row.rate_per_kg})
    db.commit()
    db.refresh(c)
    return {"status": "updated", "id": row.id}


@router.delete("/challans/{challan_id}")
def archive_challan(challan_id: int, request: Request, db: Session = Depends(get_db)):
    c = db.query(SalesChallan).filter(SalesChallan.id == challan_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Challan not found")
    if c.status != ChallanStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Only DRAFT challans can be archived")
    reason = ""
    if not _archive_record(db, request, c, "sales", "sales_challan"):
        raise HTTPException(status_code=409, detail="Challan already archived")
    _audit(db, request, "sales", "archive", record=c, record_type="sales_challan", old_values={"is_archived": False}, new_values={"is_archived": True}, notes=reason)
    db.commit()
    return {"status": "archived", "id": challan_id}


@router.post("/challans/{challan_id}/restore")
def restore_challan(challan_id: int, request: Request, db: Session = Depends(get_db)):
    c = db.query(SalesChallan).filter(SalesChallan.id == challan_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Challan not found")
    reason = ""
    if not _restore_record(db, request, c, "sales", "sales_challan"):
        raise HTTPException(status_code=409, detail="Challan is not archived")
    _audit(db, request, "sales", "restore", record=c, record_type="sales_challan", old_values={"is_archived": True}, new_values={"is_archived": False}, notes=reason)
    db.commit()
    return {"status": "restored", "id": challan_id}


@router.put("/challans/{challan_id}/status")
def update_challan_status(challan_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    c = db.query(SalesChallan).filter(SalesChallan.id == challan_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Challan not found")
    _reject_if_archived(c, "challan")
    reason = ""

    new_status = ChallanStatus(payload.get("status"))
    old_status = c.status

    if new_status == ChallanStatus.CONFIRMED and old_status != ChallanStatus.CONFIRMED:
        # Credit limit enforcement: refuse confirmation when the new
        # outstanding would exceed the customer's credit limit, unless the
        # session is in Editor Mode (then allow and audit the override).
        customer = (
            db.query(Customer).filter(Customer.id == c.customer_id).first()
            if c.customer_id else None
        )
        if customer:
            credit_limit = customer.credit_limit or D("0")
            if credit_limit > D("0"):
                projected = (customer.outstanding_balance or D("0")) + c.total_price
                if projected > credit_limit:
                    excess = projected - credit_limit
                    payload = _request_payload(request)
                    if not (payload and payload.get("editor")):
                        raise HTTPException(
                            status_code=400,
                            detail=f"Credit limit exceeded by {excess:,.2f}",
                        )
                    _audit(
                        db, request, "sales", "credit_limit_override",
                        record=c, record_type="sales_challan",
                        old_values={"credit_limit": str(credit_limit)},
                        new_values={
                            "projected_outstanding": str(projected),
                            "credit_limit_override": str(excess),
                        },
                        notes=f"Credit limit overridden by {excess:,.2f}",
                    )
        # Confirm: post stock movements and create receivable
        post_challan_sales_movements(db, c, c.items)
        c.confirmed_at = _ist_wall()
        c.outstanding_amount = c.total_price
    elif new_status == ChallanStatus.DISPATCHED:
        c.dispatched = True
        c.dispatched_at = _ist_wall()
    elif new_status == ChallanStatus.DELIVERED:
        c.delivered = True
        c.delivered_at = _ist_wall()

    c.status = new_status
    _audit(db, request, "sales", "status_change", record=c, record_type="sales_challan", old_values={"status": old_status.value}, new_values={"status": new_status.value}, notes=reason)
    db.commit()
    db.refresh(c)
    return _challan_dict(c)


@router.put("/challans/{challan_id}/reopen")
def reopen_challan(challan_id: int, request: Request, db: Session = Depends(get_db)):
    """Reopen a closed challan, restoring the status the books imply.

    Never moves stock or money. Refused when not closed (400) or archived (409).
    """
    c = db.query(SalesChallan).filter(SalesChallan.id == challan_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Challan not found")
    if c.status != ChallanStatus.CLOSED:
        raise HTTPException(status_code=400, detail="Challan is not closed")
    if c.is_archived:
        raise HTTPException(status_code=409, detail="Challan is archived")
    reason = ""
    old_status = c.status
    # create_customer_payment reduces outstanding_amount but never sets paid_amount,
    # so infer the pre-close status from outstanding_amount + payment presence.
    if c.outstanding_amount and c.outstanding_amount == c.total_price:
        new_status = ChallanStatus.CONFIRMED
    elif c.outstanding_amount and c.outstanding_amount > 0:
        new_status = ChallanStatus.PARTIALLY_PAID
    elif c.total_price and c.total_price > 0:
        # outstanding_amount == 0 with a positive total: fully paid via payments,
        # or a draft closed directly. Payments distinguish the two.
        has_payments = db.query(CustomerPayment).filter(
            CustomerPayment.challan_id == c.id,
        ).first()
        new_status = ChallanStatus.PAID if has_payments else ChallanStatus.DRAFT
    else:
        new_status = ChallanStatus.DRAFT
    c.status = new_status
    _audit(db, request, "sales", "reopen", record=c, record_type="sales_challan", old_values={"status": old_status.value}, new_values={"status": new_status.value}, notes=reason)
    db.commit()
    db.refresh(c)
    return _challan_dict(c)


@router.delete("/challans/{challan_id}/permanent")
def delete_challan_permanently(challan_id: int, request: Request, db: Session = Depends(get_db)):
    """Permanently delete a draft challan and its items.

    Refused with 409 when an invoice, payment, or stock posting blocks it.
    """
    c = db.query(SalesChallan).filter(SalesChallan.id == challan_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Challan not found")
    if c.status != ChallanStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Only draft challans can be deleted")
    invoice = db.query(SalesInvoice).filter(SalesInvoice.challan_id == challan_id).first()
    if invoice:
        raise HTTPException(status_code=409, detail=f"Cannot delete: invoice {invoice.invoice_number} exists for this challan")
    if db.query(CustomerPayment).filter(CustomerPayment.challan_id == challan_id).first():
        raise HTTPException(status_code=409, detail="Cannot delete: payments exist for this challan")
    if db.query(FinishedProductStockMovement).filter(
        FinishedProductStockMovement.reference_type == "sales_challan",
        FinishedProductStockMovement.reference_id == challan_id,
    ).first():
        raise HTTPException(status_code=409, detail="Cannot delete: stock movements exist for this challan")
    items_removed = db.query(SalesChallanItem).filter(SalesChallanItem.challan_id == challan_id).count()
    db.delete(c)
    db.commit()
    return {"status": "deleted", "items_removed": items_removed}


# Sales Invoice
@router.post("/sales-invoices", response_model=SalesInvoiceSchema)
def create_sales_invoice(payload: dict, request: Request, db: Session = Depends(get_db)):
    """Create a sales invoice from a confirmed challan."""
    challan_id = payload.get("challan_id")
    if not challan_id:
        raise HTTPException(status_code=400, detail="challan_id required")
    
    ch = db.query(SalesChallan).filter(SalesChallan.id == challan_id).first()
    if not ch:
        raise HTTPException(status_code=404, detail="Challan not found")
    if str(ch.status).lower() == "draft":
        raise HTTPException(status_code=400, detail="Challan must be confirmed first")
    
    # Check if invoice already exists
    existing = db.query(SalesInvoice).filter(SalesInvoice.challan_id == challan_id).first()
    if existing:
        raise HTTPException(status_code=409, detail="Invoice already exists for this challan")
    
    # Generate invoice number
    from backend.app.services.document_sequence import next_invoice_number
    invoice_number = next_invoice_number(db)
    
    # Calculate totals from challan
    inv = SalesInvoice(
        invoice_number=invoice_number,
        invoice_date=_ist_wall(),
        challan_id=ch.id,
        customer_id=ch.customer_id,
        intra_state=True,  # default, can be overridden
        total_quantity=ch.total_weight,
        total_weight=ch.total_weight,
        taxable_value=ch.total_price,
        total_tax=D("0"),
        cgst=D("0"),
        sgst=D("0"),
        igst=D("0"),
        grand_total=ch.total_price,
    )
    db.add(inv)
    db.flush()
    
    # Copy challan items to invoice items
    for item in ch.items:
        db.add(SalesInvoiceItem(
            invoice_id=inv.id,
            product_id=item.product_id,
            packaging_size_id=item.packaging_size_id,
            quantity=item.quantity,
            weight=item.weight,
            rate_per_kg=item.rate_per_kg,
            amount=item.amount,
        ))
    
    _audit(db, request, "sales", "invoice_create", record=inv, record_type="sales_invoice", 
           new_values={"invoice_number": invoice_number, "challan_id": ch.id})
    db.commit()
    db.refresh(inv)
    return inv


@router.get("/invoices")
def list_sales_invoices(db: Session = Depends(get_db)):
    """List sales invoices with customer and challan for the invoices page."""
    rows = (
        db.query(SalesInvoice, Customer, SalesChallan)
        .join(Customer, SalesInvoice.customer_id == Customer.id)
        .join(SalesChallan, SalesInvoice.challan_id == SalesChallan.id)
        .order_by(SalesInvoice.id.desc())
        .all()
    )
    return [
        {
            "id": inv.id,
            "invoice_number": inv.invoice_number,
            "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
            "created_at": inv.invoice_date.isoformat() if inv.invoice_date else None,
            "customer_name": cust.name,
            "challan_number": ch.challan_number,
            "challan_id": ch.id,
            "grand_total": float(inv.grand_total or 0),
            "cgst": float(inv.cgst or 0),
            "sgst": float(inv.sgst or 0),
            "igst": float(inv.igst or 0),
        }
        for inv, cust, ch in rows
    ]


@router.get("/invoices/{inv_id}")
def get_sales_invoice(inv_id: int, db: Session = Depends(get_db)):
    """Get a single sales invoice with its line items (for the print view)."""
    inv = db.query(SalesInvoice).filter(SalesInvoice.id == inv_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return _invoice_to_dict(inv, db)


def search_all(db, q: str = "", include_payments: bool = True, include_expenses: bool = True) -> dict:
    """Global search across masters, challans, invoices, payments and expenses.

    Payments are matched by their display code (RCPT-##### / PAY-#####).
    Expenses are matched by description, capped at 8. Blank query returns
    empty groups. The flags let callers (e.g. a restricted profile) drop
    whole groups.
    """
    q = (q or "").strip()
    result = {
        "customers": [],
        "challans": [],
        "invoices": [],
        "products": [],
        "suppliers": [],
        "payments": [],
        "expenses": [],
    }
    if not q:
        return result
    like = f"%{q}%"

    customers = db.query(Customer).filter(Customer.name.ilike(like)).limit(20).all()
    result["customers"] = [
        {"name": c.name, "company": c.company_name or "", "contact": c.contact_number or ""}
        for c in customers
    ]

    challans = (
        db.query(SalesChallan)
        .filter(SalesChallan.challan_number.ilike(like))
        .limit(20)
        .all()
    )
    result["challans"] = [
        {
            "number": c.challan_number,
            "customer": c.customer.name if c.customer else "",
            "status": c.status or "",
        }
        for c in challans
    ]

    invoices = (
        db.query(SalesInvoice)
        .filter(SalesInvoice.invoice_number.ilike(like))
        .limit(20)
        .all()
    )
    result["invoices"] = [{"number": inv.invoice_number, "supplier": ""} for inv in invoices]

    products = db.query(Product).filter(Product.name.ilike(like)).limit(20).all()
    result["products"] = [{"name": p.name} for p in products]

    suppliers = db.query(Supplier).filter(Supplier.name.ilike(like)).limit(20).all()
    result["suppliers"] = [
        {"name": s.name, "company": s.company_name or "", "contact": s.contact_number or ""}
        for s in suppliers
    ]

    if include_payments:
        code = re.match(r"^(RCPT|PAY)-(\d{1,6})$", q)
        if code:
            kind, num = code.group(1), int(code.group(2))
            if kind == "RCPT":
                p = db.query(CustomerPayment).filter(CustomerPayment.id == num).first()
                if p:
                    ch = db.query(SalesChallan).filter(SalesChallan.id == p.challan_id).first()
                    result["payments"] = [
                        {
                            "code": f"RCPT-{p.id:04d}",
                            "kind": "receipt",
                            "id": p.id,
                            "challan_id": p.challan_id,
                            "invoice_id": None,
                            "customer": ch.customer.name if ch and ch.customer else "",
                            "supplier": "",
                            "amount": float(p.amount or 0),
                            "date": p.payment_date.isoformat() if p.payment_date else None,
                        }
                    ]
            else:
                p = db.query(SupplierPayment).filter(SupplierPayment.id == num).first()
                if p:
                    inv = db.query(PurchaseInvoice).filter(PurchaseInvoice.id == p.purchase_invoice_id).first()
                    result["payments"] = [
                        {
                            "code": f"PAY-{p.id:04d}",
                            "kind": "payment",
                            "id": p.id,
                            "challan_id": None,
                            "invoice_id": p.purchase_invoice_id,
                            "customer": "",
                            "supplier": inv.supplier.name if inv and inv.supplier else "",
                            "amount": float(p.amount or 0),
                            "date": p.payment_date.isoformat() if p.payment_date else None,
                        }
                    ]

    if include_expenses:
        expense_rows = (
            db.query(Expense, ExpenseCategory)
            .outerjoin(ExpenseCategory, Expense.category_id == ExpenseCategory.id)
            .filter(Expense.description.ilike(like))
            .order_by(Expense.date.desc())
            .limit(8)
            .all()
        )
        result["expenses"] = [
            {
                "description": e.description,
                "category": c.name if c else "",
                "date": e.date.isoformat() if e.date else None,
                "amount": float(e.amount or 0),
            }
            for e, c in expense_rows
        ]

    return result


# Customer Payment
@router.post("/customer-payments", response_model=CustomerPaymentSchema)
def create_customer_payment(payload: dict, request: Request, db: Session = Depends(get_db)):
    """Record a customer payment against a challan."""
    challan_id = payload.get("challan_id")
    amount = payload.get("amount")
    payment_date = payload.get("payment_date")
    payment_mode = payload.get("payment_mode", "cash")
    transaction_ref = payload.get("transaction_ref")
    notes = payload.get("notes")
    
    if not challan_id or amount is None:
        raise HTTPException(status_code=400, detail="challan_id and amount required")
    
    ch = db.query(SalesChallan).filter(SalesChallan.id == challan_id).first()
    if not ch:
        raise HTTPException(status_code=404, detail="Challan not found")
    
    # Parse date
    if isinstance(payment_date, str):
        payment_date = datetime.fromisoformat(payment_date).date()
    elif payment_date is None:
        payment_date = _ist_wall().date()
    
    pay = CustomerPayment(
        challan_id=ch.id,
        payment_date=payment_date,
        amount=amount,
        payment_mode=payment_mode,
        transaction_ref=transaction_ref,
        notes=notes,
    )
    db.add(pay)
    
    # Update outstanding amount
    ch.outstanding_amount = max(D("0"), (ch.outstanding_amount or D("0")) - amount)
    ch.paid_amount = (ch.paid_amount or D("0")) + amount
    if ch.outstanding_amount == D("0"):
        ch.status = ChallanStatus.PAID
    elif ch.outstanding_amount < ch.total_price:
        ch.status = ChallanStatus.PARTIALLY_PAID
    
    _audit(db, request, "sales", "payment_create", record=pay, record_type="customer_payment",
           new_values={"challan_id": ch.id, "amount": float(amount)})
    db.commit()
    db.refresh(pay)
    return {"amount": float(pay.amount), "id": pay.id, "challan_id": pay.challan_id, "payment_date": pay.payment_date.isoformat() if pay.payment_date else None, "payment_mode": pay.payment_mode}


@router.put("/customer-payments/{payment_id}")
def update_payment(payment_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    """Edit a customer payment; change note required."""
    pay = db.query(CustomerPayment).filter(CustomerPayment.id == payment_id).first()
    if not pay:
        raise HTTPException(status_code=404, detail="Payment not found")
    reason = _change_reason(request, payload)
    _reject_if_archived(pay, "customer payment")
    old_amount = pay.amount
    if "amount" in payload and payload["amount"] is not None:
        pay.amount = D(str(payload["amount"]))
    if "payment_mode" in payload and payload["payment_mode"] is not None:
        pay.payment_mode = payload["payment_mode"]
    if "notes" in payload:
        pay.notes = payload["notes"]
    # Recompute challan outstanding/paid from the delta
    ch = db.query(SalesChallan).filter(SalesChallan.id == pay.challan_id).first()
    if ch:
        headroom = (ch.total_price or D("0")) - (ch.paid_amount or D("0")) + old_amount
        if pay.amount > headroom:
            raise HTTPException(status_code=400, detail="Payment exceeds challan total")
        delta = pay.amount - old_amount
        ch.outstanding_amount = max(D("0"), (ch.outstanding_amount or D("0")) - delta)
        ch.paid_amount = max(D("0"), (ch.paid_amount or D("0")) + delta)
        if ch.outstanding_amount == D("0"):
            ch.status = ChallanStatus.PAID
        elif ch.outstanding_amount < ch.total_price:
            ch.status = ChallanStatus.PARTIALLY_PAID
        else:
            ch.status = ChallanStatus.CONFIRMED
    _audit(db, request, "sales", "update", record=pay, record_type="customer_payment",
           old_values={"amount": float(old_amount)}, new_values={"amount": float(pay.amount)}, notes=reason)
    db.commit()
    db.refresh(pay)
    return {"amount": float(pay.amount), "id": pay.id, "challan_id": pay.challan_id, "payment_date": pay.payment_date.isoformat() if pay.payment_date else None, "payment_mode": pay.payment_mode}


@router.delete("/customer-payments/{payment_id}")
def delete_payment(payment_id: int, request: Request, db: Session = Depends(get_db)):
    """Archive (soft-delete) a customer payment; change note required."""
    """Archive (soft-delete) a customer payment; change note required."""
    pay = db.query(CustomerPayment).filter(CustomerPayment.id == payment_id).first()
    if not pay:
        raise HTTPException(status_code=404, detail="Payment not found")
    reason = _change_reason(request)
    pay.is_archived = True
    _audit(db, request, "sales", "archive", record=pay, record_type="customer_payment",
           old_values={"is_archived": False}, new_values={"is_archived": True}, notes=reason)
    db.commit()
    return {"status": "archived", "id": payment_id}


@router.get("/finished-stock")
def list_finished_stock(db: Session = Depends(get_db)):
    """Reconciles with /finished-stock/balance: the balance rows' totals must
    sum to these product totals (guard test: test_agrees_with_the_existing_
    per_product_endpoint)."""
    products = {p.id: p for p in db.query(Product).filter(Product.is_active == True).all()}  # noqa: E712
    sizes = {s.id: s for s in db.query(PackagingSize).all()}
    totals = {}
    for r in (
        db.query(
            FinishedProductStockMovement.product_id,
            FinishedProductStockMovement.packaging_size_id,
            FinishedProductStockMovement.movement_type,
            func.sum(FinishedProductStockMovement.quantity).label("qty"),
        )
        .group_by(
            FinishedProductStockMovement.product_id,
            FinishedProductStockMovement.packaging_size_id,
            FinishedProductStockMovement.movement_type,
        )
        .all()
    ):
        if r.product_id not in products:
            continue
        size = sizes.get(r.packaging_size_id)
        wkg = float(size.weight_in_kg or 0) if size else 0.0
        q = float(r.qty or 0)
        if r.movement_type in (FinishedProductMovementType.PRODUCTION, FinishedProductMovementType.OPENING, FinishedProductMovementType.ADJUSTMENT):
            delta = q
        elif r.movement_type in (
            FinishedProductMovementType.SALES,
            FinishedProductMovementType.WASTAGE,
            FinishedProductMovementType.RETURN,
        ):
            delta = -q
        else:
            delta = 0.0
        totals[r.product_id] = totals.get(r.product_id, 0.0) + delta
    return [
        {
            "product_id": p.id,
            "product_name": p.name,
            "total_kg": totals.get(p.id, 0.0),
        }
        for p in products.values()
    ]


@router.get("/finished-stock/balance")
def finished_stock_balance(db: Session = Depends(get_db)):
    """Current finished stock balance per product/size.

    The ledger stores kg; packets derive from each size's own weight_in_kg.
    Every size of every active product is listed (zero balances included);
    an inactive size appears only while it still holds history. Returns
    {rows, meta} — dashboard, challans and inventory all read d.rows.
    """
    products = {p.id: p for p in db.query(Product).filter(Product.is_active == True).all()}  # noqa: E712
    sizes = {s.id: s for s in db.query(PackagingSize).all()}

    agg = {}
    for r in (
        db.query(
            FinishedProductStockMovement.product_id,
            FinishedProductStockMovement.packaging_size_id,
            FinishedProductStockMovement.movement_type,
            func.sum(FinishedProductStockMovement.quantity).label("qty"),
        )
        .group_by(
            FinishedProductStockMovement.product_id,
            FinishedProductStockMovement.packaging_size_id,
            FinishedProductStockMovement.movement_type,
        )
        .all()
    ):
        slot = agg.setdefault((r.product_id, r.packaging_size_id), {"stock_kg": 0.0, "sold_kg": 0.0, "out_kg": 0.0})
        q = float(r.qty or 0)
        if r.movement_type in (FinishedProductMovementType.PRODUCTION, FinishedProductMovementType.OPENING, FinishedProductMovementType.ADJUSTMENT):
            slot["stock_kg"] += q
        elif r.movement_type == FinishedProductMovementType.SALES:
            slot["sold_kg"] += q
            slot["out_kg"] += q
        elif r.movement_type in (
            FinishedProductMovementType.WASTAGE,
            FinishedProductMovementType.RETURN,
        ):
            slot["out_kg"] += q

    out = []
    negative = 0
    tot = {"stock_kg": 0.0, "sold_kg": 0.0, "balance_kg": 0.0, "stock_packets": 0.0, "sold_packets": 0.0, "balance_packets": 0.0}
    for prod in products.values():
        for size in (s for s in sizes.values() if s.product_id == prod.id):
            slot = agg.get(
                (prod.id, size.id), {"stock_kg": 0.0, "sold_kg": 0.0, "out_kg": 0.0}
            )
            if not size.is_active and (prod.id, size.id) not in agg:
                continue  # retired size with no history: nothing to show
            wkg = float(size.weight_in_kg or 0)
            stock_kg = slot["stock_kg"]
            sold_kg = slot["sold_kg"]
            bal_kg = stock_kg - slot["out_kg"]
            bal_pkts = (bal_kg / wkg) if wkg else 0.0
            is_neg = bal_kg < 0
            if is_neg:
                negative += 1
            tot["stock_kg"] += stock_kg
            tot["sold_kg"] += sold_kg
            tot["balance_kg"] += bal_kg
            tot["stock_packets"] += (stock_kg / wkg) if wkg else 0.0
            tot["sold_packets"] += (sold_kg / wkg) if wkg else 0.0
            tot["balance_packets"] += bal_pkts
            out.append({
                "product_id": prod.id,
                "product_name": prod.name,
                "size_id": size.id,
                "packaging_size_id": size.id,
                "size_name": size.size_name,
                "weight_in_kg": wkg,
                "pack_weight_kg": wkg,
                "stock_packets": (stock_kg / wkg) if wkg else 0.0,
                "stock_kg": stock_kg,
                "sold_packets": (sold_kg / wkg) if wkg else 0.0,
                "sold_kg": sold_kg,
                "balance_packets": bal_pkts,
                "balance_kg": bal_kg,
                "is_negative": is_neg,
            })
    return {"rows": out, "totals": tot, "meta": {"negative_rows": negative}}


@router.post("/finished-stock/opening")
def record_opening_stock(payload: dict, request: Request, db: Session = Depends(get_db)):
    """Record opening finished stock."""
    product_id = (payload or {}).get("product_id")
    packaging_size_id = (payload or {}).get("packaging_size_id")
    packets = (payload or {}).get("packets", 0)

    if not product_id or not packaging_size_id:
        raise HTTPException(status_code=400, detail="product_id and packaging_size_id required")

    prod = db.query(Product).filter(Product.id == product_id).first()
    if not prod:
        raise HTTPException(status_code=404, detail="Product not found")
    size = db.query(PackagingSize).filter(PackagingSize.id == packaging_size_id).first()
    if not size:
        raise HTTPException(status_code=404, detail="Packaging size not found")

    movement = FinishedProductStockMovement(
        product_id=product_id,
        packaging_size_id=packaging_size_id,
        movement_type=FinishedProductMovementType.OPENING,
        quantity=float(packets or 0) * float(size.weight_in_kg or 0),
        packets=int(packets or 0),
        notes="Opening stock",
    )
    db.add(movement)
    _audit(db, request, "inventory", "opening_stock", record=movement, record_type="finished_product_stock_movement", new_values={"product": prod.name, "size": size.size_name, "packets": packets})
    db.commit()
    return {"status": "recorded", "id": movement.id}


# Stock adjustments
@router.get("/stock-adjustments/reasons")
def stock_adjustment_reasons(db: Session = Depends(get_db)):
    """Distinct adjustment reasons from finished-goods and raw-material
    movements, most-recent-first, case-insensitive dedup."""
    rows = (
        db.query(FinishedProductStockMovement.notes)
        .filter(FinishedProductStockMovement.movement_type == "adjustment",
                FinishedProductStockMovement.notes.isnot(None))
        .all()
    ) + (
        db.query(RawMaterialStockMovement.notes)
        .filter(RawMaterialStockMovement.movement_type == "adjustment",
                RawMaterialStockMovement.notes.isnot(None))
        .all()
    )
    reasons, seen = [], set()
    for (note,) in rows:
        key = (note or "").strip().casefold()
        if key and key not in seen:
            seen.add(key)
            reasons.append((note or "").strip())
    return {"reasons": reasons}


@router.get("/stock-adjustments")
def list_stock_adjustments(
    db: Session = Depends(get_db),
    from_date: str | None = None,
    to_date: str | None = None,
):
    """Unified adjustment history (finished goods + raw materials), newest first.

    One list feeds both the Inventory and Raw Materials pages; each row is
    tagged kind ("Finished Goods"/"Raw Material") and pages filter on it.
    Optional ?from_date=&to_date= bounds the window on when the adjustment
    was recorded (created_at), inclusive of both ends.
    """
    from datetime import datetime

    fstart = datetime.fromisoformat(from_date) if from_date else None
    tend = None
    if to_date:
        tend = datetime.combine(date.fromisoformat(to_date), datetime.max.time())

    fg_sizes = {s.id: s.size_name for s in db.query(PackagingSize).all()}
    rows = []
    fq = db.query(FinishedProductStockMovement).filter(
        FinishedProductStockMovement.movement_type == FinishedProductMovementType.ADJUSTMENT
    )
    if fstart:
        fq = fq.filter(FinishedProductStockMovement.created_at >= fstart)
    if tend:
        fq = fq.filter(FinishedProductStockMovement.created_at <= tend)
    for m in fq.all():
        prod_name = m.product.name if m.product else None
        if prod_name:
            size_name = fg_sizes.get(m.packaging_size_id)
            item = f"{prod_name} ({size_name})" if size_name else prod_name
        else:
            item = f"#{m.product_id}"
        created = m.created_at.replace(tzinfo=None) if m.created_at and m.created_at.tzinfo else m.created_at
        rows.append({
            "id": f"fin-{m.id}",
            "date": m.created_at.isoformat() if m.created_at else None,
            "kind": "Finished Goods",
            "item": item,
            "quantity": float(m.quantity or 0),
            "unit": "kg",
            "reason": m.notes,
            "_sort": (created, m.id),
        })
    rq = db.query(RawMaterialStockMovement).filter(
        RawMaterialStockMovement.movement_type == "adjustment"
    )
    if fstart:
        rq = rq.filter(RawMaterialStockMovement.created_at >= fstart)
    if tend:
        rq = rq.filter(RawMaterialStockMovement.created_at <= tend)
    for m in rq.all():
        item = m.raw_material.name if (m.raw_material and m.raw_material.name) else f"#{m.raw_material_id}"
        created = m.created_at.replace(tzinfo=None) if m.created_at and m.created_at.tzinfo else m.created_at
        rows.append({
            "id": f"raw-{m.id}",
            "date": m.created_at.isoformat() if m.created_at else None,
            "kind": "Raw Material",
            "item": item,
            "quantity": float(m.quantity or 0),
            "unit": m.unit or "",
            "reason": m.notes,
            "_sort": (created, m.id),
        })
    rows.sort(key=lambda r: (r["_sort"][0] or datetime.min, r["_sort"][1]), reverse=True)
    for r in rows:
        r.pop("_sort", None)
    return rows


@router.post("/stock-adjustments")
def create_stock_adjustment(payload: dict, request: Request, db: Session = Depends(get_db)):
    """Record a manual adjustment — finished goods (product+size) or raw material."""
    payload = payload or {}
    reason = payload.get("reason") or "Manual adjustment"

    if payload.get("kind") == "raw_material":
        rm_id = payload.get("raw_material_id")
        if not rm_id:
            raise HTTPException(status_code=400, detail="raw_material_id required")
        if payload.get("entry_unit") == "packets":
            raise HTTPException(status_code=400, detail="Packet counts apply to finished goods only")
        rm = db.query(RawMaterial).filter(RawMaterial.id == rm_id).first()
        if not rm:
            raise HTTPException(status_code=404, detail="Raw material not found")
        movement = RawMaterialStockMovement(
            raw_material_id=rm_id,
            movement_type="adjustment",
            quantity=payload.get("quantity", 0),
            unit=str(getattr(rm.unit, "value", rm.unit) or ""),
            notes=reason,
        )
        db.add(movement)
        # Keep the live current_stock in sync (production consumption already
        # does this; purchases/adjustments were the missing writers).
        rm.current_stock = (rm.current_stock or D("0")) + D(str(payload.get("quantity", 0)))
        _audit(db, request, "inventory", "manual_adjustment", record=movement, record_type="raw_material_stock_movement", new_values={"material": rm.name, "qty": payload.get("quantity"), "reason": reason})
        db.commit()
        return {"status": "recorded", "id": movement.id}

    product_id = payload.get("product_id")
    packaging_size_id = payload.get("packaging_size_id")
    qty = payload.get("quantity", payload.get("qty", 0))
    entry_unit = payload.get("entry_unit")
    if not product_id or not packaging_size_id:
        raise HTTPException(status_code=400, detail="product_id and packaging_size_id required")

    prod = db.query(Product).filter(Product.id == product_id).first()
    if not prod:
        raise HTTPException(status_code=404, detail="Product not found")
    size = db.query(PackagingSize).filter(PackagingSize.id == packaging_size_id).first()
    if not size:
        raise HTTPException(status_code=404, detail="Packaging size not found")

    if entry_unit == "packets":
        # Packet entry: whole packets x the size's own weight, exact on the row
        try:
            float_qty = float(qty or 0)
        except (TypeError, ValueError):
            float_qty = 0.0
        if float_qty % 1:
            raise HTTPException(status_code=400, detail="Packets must be whole numbers")
        wkg = _ch_dec(size.weight_in_kg)
        if not wkg:
            raise HTTPException(status_code=400, detail="This size has no packet weight — enter kilograms instead")
        pkts = int(float_qty)
        qty_kg = pkts * wkg
        def _fmt(d):
            return format(D(d).normalize(), "f")
        conv = f"{_fmt(pkts)} pkt = {_fmt(qty_kg)} kg"
        notes = f"{conv}; {reason}" if reason else conv
    else:
        # kg/legacy entry: store the quantity exactly as sent
        qty_kg = _ch_dec(qty)
        pkts = 0
        notes = reason

    # Optional date the adjustment actually happened (for backdated entries).
    # Defaults to NULL; created_at always records when it was entered.
    movement_date = None
    movement_date_str = payload.get("movement_date")
    if movement_date_str:
        try:
            movement_date = datetime.fromisoformat(str(movement_date_str))
        except ValueError:
            movement_date = None

    movement = FinishedProductStockMovement(
        product_id=product_id,
        packaging_size_id=packaging_size_id,
        movement_type=FinishedProductMovementType.ADJUSTMENT,
        quantity=qty_kg,
        packets=pkts,
        movement_date=movement_date,
        notes=notes,
    )
    db.add(movement)
    _audit(db, request, "inventory", "manual_adjustment", record=movement, record_type="finished_product_stock_movement", new_values={"product": prod.name, "size": size.size_name, "qty": qty, "unit": entry_unit, "reason": reason})
    db.commit()
    return {"status": "recorded", "id": movement.id}


# Analysis routes
@router.get("/analysis/stock-sales")
def analysis_stock_sales(
    from_date: str | None = None,
    to_date: str | None = None,
    product_id: int | None = None,
    db: Session = Depends(get_db),
):
    """Stock vs sales analysis.

    Lists every active product x size (zero balances included) so the report is
    not blank when there are no transactions yet. Stock and sale are both
    computed in kg, then converted to packets via pack weight, so BAL =
    stock - sale is in consistent units (packets). Mirrors
    finished_stock_balance's unit handling.
    """
    from datetime import datetime
    from sqlalchemy import case, func

    # Columns: every distinct pack-size signature (name + pack weight), so a
    # common size shared by several products (e.g. "50g (0.05 kg)") is one
    # column instead of one column per product. Selecting it filters every
    # product that carries that size.
    def _size_sig(s):
        wkg = float(s.weight_in_kg or 0)
        return f"{s.size_name} ({wkg:g} kg)" if wkg else s.size_name

    sizes = (
        db.query(PackagingSize)
        .filter(PackagingSize.is_active == True)  # noqa: E712
        .order_by(PackagingSize.id)
        .all()
    )
    size_sig = {s.id: _size_sig(s) for s in sizes}
    sig_to_sizes = {}
    for s in sizes:
        sig_to_sizes.setdefault(size_sig[s.id], []).append(s.id)
    # Unique signatures, key == label == signature (deduped across products).
    columns = [{"key": sig, "label": sig} for sig in sig_to_sizes]
    size_map = {s.id: s for s in sizes}

    # Every active product, so products without any transaction still appear.
    products = {p.id: p for p in db.query(Product).filter(Product.is_active == True).all()}  # noqa: E712
    product_sigs = {}
    for s in sizes:
        product_sigs.setdefault(s.product_id, set()).add(size_sig[s.id])

    # Date filter applied to sales (challan date). Stock is the current balance.
    date_filters = [SalesChallan.is_archived == False]  # noqa: E712
    if from_date:
        date_filters.append(SalesChallan.challan_date >= datetime.fromisoformat(from_date).date())
    if to_date:
        date_filters.append(SalesChallan.challan_date <= datetime.fromisoformat(to_date).date())

    # Net stock kg per (product, signature): production/opening add, everything else removes.
    # Multiple sizes of a product sharing a signature are summed into one bucket.
    stock_by_size = {}
    for r in (
        db.query(
            FinishedProductStockMovement.product_id,
            FinishedProductStockMovement.packaging_size_id,
            func.sum(
                case(
                    (
                        FinishedProductStockMovement.movement_type.in_(
                            [
                                FinishedProductMovementType.PRODUCTION,
                                FinishedProductMovementType.OPENING,
                                FinishedProductMovementType.ADJUSTMENT,
                            ]
                        ),
                        FinishedProductStockMovement.quantity,
                    ),
                    else_=-FinishedProductStockMovement.quantity,
                )
            ).label("net_kg"),
        )
        .group_by(
            FinishedProductStockMovement.product_id,
            FinishedProductStockMovement.packaging_size_id,
        )
        .all()
    ):
        sig = size_sig.get(r.packaging_size_id)
        if sig is None:
            continue
        key = (r.product_id, sig)
        stock_by_size[key] = stock_by_size.get(key, 0.0) + float(r.net_kg or 0)

    # Sale kg per (product, signature) within the date range.
    sale_by_size = {}
    for r in (
        db.query(
            SalesChallanItem.product_id,
            SalesChallanItem.packaging_size_id,
            func.sum(SalesChallanItem.weight).label("sale_kg"),
        )
        .join(SalesChallan)
        .filter(*date_filters)
        .group_by(SalesChallanItem.product_id, SalesChallanItem.packaging_size_id)
        .all()
    ):
        sig = size_sig.get(r.packaging_size_id)
        if sig is None:
            continue
        key = (r.product_id, sig)
        sale_by_size[key] = sale_by_size.get(key, 0.0) + float(r.sale_kg or 0)

    rows = []
    for pid in sorted(products):
        if product_id and pid != product_id:
            continue
        prod = products[pid]
        buckets = {}
        total_ready_kg = 0.0
        activity = {sig for (p, sig) in list(stock_by_size.keys()) + list(sale_by_size.keys()) if p == pid}
        involved = activity | product_sigs.get(pid, set())
        for sig in involved:
            size_ids = sig_to_sizes.get(sig, [])
            # All sizes sharing a signature have the same pack weight (it's part
            # of the signature); take it from the first size.
            wkg = 0.0
            if size_ids:
                s0 = size_map.get(size_ids[0])
                wkg = float(s0.weight_in_kg or 0) if s0 else 0.0
            sk = stock_by_size.get((pid, sig), 0.0)
            sl = sale_by_size.get((pid, sig), 0.0)
            bal = sk - sl
            stock_pkts = (sk / wkg) if wkg else 0.0
            sale_pkts = (sl / wkg) if wkg else 0.0
            bal_pkts = (bal / wkg) if wkg else 0.0
            total_ready_kg += bal
            buckets[sig] = {
                "stock_packets": stock_pkts,
                "sale_packets": sale_pkts,
                "bal_packets": bal_pkts,
                "bal_kg": bal,
                "pack_weight_kg": wkg,
                "sizes": [],
            }
        rows.append(
            {
                "product_id": pid,
                "product_name": prod.name,
                "category": prod.category,
                "total_ready_kg": round(total_ready_kg, 3),
                "buckets": buckets,
            }
        )

    return {"columns": columns, "rows": rows}


@router.get("/analysis/transactions")
def analysis_transactions(
    product_id: str | None = None,
    bucket: str | None = None,
    size: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    db: Session = Depends(get_db),
):
    """Transaction drill-down for one product.

    Frontend (analysis.html drillDown, monthly-sales.html drillMonth) calls
    this with ?product_id=&bucket=sales|production|movements|all&size=&from_date=&to_date=
    and expects {product_name, sales, production, movements} where:

    sales:    {challan, date, customer, packets, weight_kg, amount}
    production: {number, date, target_kg, actual_kg, status}
    movements: {date, type, reference, quantity_kg}

    Buckets default to "all" when omitted. An empty product_id means "all products".
    size is an optional pack-size signature (e.g. "50g (0.05 kg)") that filters
    all three transaction lists to that size.
    """
    from datetime import datetime, time

    # product_id arrives as a query string; "" (frontend "all products") means None.
    pid = None
    if product_id and str(product_id).strip():
        try:
            pid = int(product_id)
        except (TypeError, ValueError):
            pid = None
    bucket = (bucket or "all").strip() or "all"

    # size arrives as a pack-size signature (e.g. "50g (0.05 kg)") from the
    # analysis page BAL cell. Resolve it to packaging_size ids so the drill
    # down shows only transactions for that size.
    size_ids: list[int] = []
    size_requested = bool(size and str(size).strip())
    if size_requested:
        want = str(size).strip()
        for s in db.query(PackagingSize).all():
            wkg = float(s.weight_in_kg or 0)
            sig = f"{s.size_name} ({wkg:g} kg)" if wkg else s.size_name
            if sig == want:
                size_ids.append(s.id)

    product = None
    product_name = None
    if pid:
        product = db.query(Product).filter(Product.id == pid).first()
        product_name = product.name if product else None

    def _parse(s):
        return datetime.fromisoformat(s).date() if s else None

    fdate = _parse(from_date)
    tdate = _parse(to_date)
    tdate_end = datetime.combine(tdate + timedelta(days=1), time.min) if tdate else None

    want_sales = bucket in (None, "all", "sales")
    want_production = bucket in (None, "all", "production")
    want_movements = bucket in (None, "all", "movements")

    sales = []
    if want_sales:
        q = (
            db.query(SalesChallan)
            .join(SalesChallanItem, SalesChallanItem.challan_id == SalesChallan.id)
            .filter(SalesChallan.is_archived == False)  # noqa: E712
        )
        if pid:
            q = q.filter(SalesChallanItem.product_id == pid)
        if fdate:
            q = q.filter(SalesChallan.challan_date >= fdate)
        if tdate_end:
            q = q.filter(SalesChallan.challan_date < tdate_end)
        if size_ids:
            q = q.filter(SalesChallanItem.packaging_size_id.in_(size_ids))
        elif size_requested:
            q = q.filter(false())
        for ch in q.all():
            prod_items = [
                it for it in (ch.items or [])
                if not pid or it.product_id == pid
            ]
            sales.append({
                "challan": ch.challan_number,
                "date": ch.challan_date.isoformat() if ch.challan_date else None,
                "customer": ch.customer.name if ch.customer else None,
                "packets": sum(float(it.quantity or 0) for it in prod_items),
                "weight_kg": sum(float(it.weight or 0) for it in prod_items),
                "amount": sum(float(it.amount or 0) for it in prod_items),
            })

    production = []
    if want_production:
        q = db.query(ProductionEntry).filter(ProductionEntry.is_archived == False)  # noqa: E712
        if pid:
            q = q.filter(ProductionEntry.product_id == pid)
        if fdate:
            q = q.filter(ProductionEntry.production_date >= fdate)
        if tdate_end:
            q = q.filter(ProductionEntry.production_date < tdate_end)
        if size_ids:
            q = q.filter(ProductionEntry.packaging_size_id.in_(size_ids))
        elif size_requested:
            q = q.filter(false())
        for e in q.all():
            production.append({
                "number": e.production_number,
                "date": e.production_date.isoformat() if e.production_date else None,
                "target_kg": float(e.target_quantity or 0),
                "actual_kg": float(e.actual_output or 0),
                "status": e.status.value if e.status else "",
            })

    movements = []
    if want_movements:
        q = db.query(FinishedProductStockMovement)
        if pid:
            q = q.filter(FinishedProductStockMovement.product_id == pid)
        if fdate:
            q = q.filter(FinishedProductStockMovement.movement_date >= fdate)
        if tdate_end:
            q = q.filter(FinishedProductStockMovement.movement_date < tdate_end)
        if size_ids:
            q = q.filter(FinishedProductStockMovement.packaging_size_id.in_(size_ids))
        elif size_requested:
            q = q.filter(false())
        for m in q.all():
            movements.append({
                "date": (m.movement_date or m.created_at).isoformat(),
                "type": m.movement_type.value if m.movement_type else "",
                "reference": (
                    f"{m.reference_type} #{m.reference_id}"
                    if m.reference_type and m.reference_id else ""
                ),
                "quantity_kg": float(m.quantity or 0),
            })

    sales.sort(key=lambda x: x["date"] or "", reverse=True)
    production.sort(key=lambda x: x["date"] or "", reverse=True)
    movements.sort(key=lambda x: x["date"] or "", reverse=True)

    return {
        "product_name": product_name,
        "sales": sales,
        "production": production,
        "movements": movements,
    }


@router.get("/analysis/export")
def analysis_export(
    from_date: str | None = None,
    to_date: str | None = None,
    db: Session = Depends(get_db),
):
    """Export all transactions as CSV."""
    from datetime import datetime
    from backend.app.models import PurchaseInvoice, SalesChallan, ProductionEntry

    rows = []

    q = db.query(PurchaseInvoice).filter(PurchaseInvoice.is_archived == False)  # noqa: E712
    if from_date:
        q = q.filter(PurchaseInvoice.invoice_date >= datetime.fromisoformat(from_date).date())
    if to_date:
        q = q.filter(PurchaseInvoice.invoice_date <= datetime.fromisoformat(to_date).date())
    for inv in q.all():
        rows.append(["Purchase", inv.invoice_number, inv.invoice_date, inv.supplier.name if inv.supplier else "", float(inv.total_amount or 0)])

    q = db.query(SalesChallan).filter(SalesChallan.is_archived == False)  # noqa: E712
    if from_date:
        q = q.filter(SalesChallan.challan_date >= datetime.fromisoformat(from_date).date())
    if to_date:
        q = q.filter(SalesChallan.challan_date <= datetime.fromisoformat(to_date).date())
    for ch in q.all():
        rows.append(["Sale", ch.challan_number, ch.date, ch.customer.name if ch.customer else "", float(ch.total_price or 0)])

    q = db.query(ProductionEntry).filter(ProductionEntry.is_archived == False)  # noqa: E712
    if from_date:
        q = q.filter(ProductionEntry.created_at >= datetime.fromisoformat(from_date))
    if to_date:
        q = q.filter(ProductionEntry.created_at <= datetime.fromisoformat(to_date) + timedelta(days=1))
    for e in q.all():
        rows.append(["Production", e.production_number, e.created_at.date() if e.created_at else "", e.product.name if e.product else "", 0])

    # Return as text/csv
    from fastapi.responses import PlainTextResponse
    import csv
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Type", "Number", "Date", "Party", "Amount"])
    writer.writerows(rows)
    return PlainTextResponse(output.getvalue(), media_type="text/csv")


@router.get("/analysis/monthly-sales")
def monthly_sales_analysis(
    fy: int | None = None,
    year: int | None = None,
    db: Session = Depends(get_db),
):
    """Monthly sales grouped by financial year (Apr-Mar) or calendar year.

    Frontend sends either ``?fy=YYYY`` (FY start year) or ``?year=YYYY``
    (calendar year). Returns {fy_start, fy_end, products, product_ids, months}
    where each month has {key, label, month_total_amount, month_total_weight,
    products: {name: {amount, weight}}}.
    """
    now = _ist_wall()
    cur_year = now.year

    if fy is not None:
        fy_start = fy
        month_pairs = [((m if m <= 12 else m - 12), fy_start if m <= 12 else fy_start + 1) for m in range(4, 16)]
    elif year is not None:
        fy_start = year
        month_pairs = [(m, year) for m in range(1, 13)]
    else:
        fy_start = cur_year if now.month >= 4 else cur_year - 1
        month_pairs = [((m if m <= 12 else m - 12), fy_start if m <= 12 else fy_start + 1) for m in range(4, 16)]
    fy_end = fy_start + 1

    # All active products (so zero-sale products still appear as columns).
    products = (
        db.query(Product)
        .filter(Product.is_active == True)  # noqa: E712
        .order_by(Product.name)
        .all()
    )
    product_names = [p.name for p in products]
    product_ids = {p.name: p.id for p in products}

    months = []
    for month, y in month_pairs:
        month_start = date(y, month, 1)
        month_end = date(y, month, 1) + timedelta(days=32)
        month_end = date(month_end.year, month_end.month, 1) - timedelta(days=1)

        challans = db.query(SalesChallan).filter(
            SalesChallan.challan_date >= month_start,
            SalesChallan.challan_date <= month_end,
            SalesChallan.is_archived == False,  # noqa: E712
        ).all()

        # Per-product aggregation for this month.
        prod_data = {}  # name -> {amount, weight}
        month_total_amount = 0.0
        month_total_weight = 0.0
        for ch in challans:
            for item in ch.items:
                name = item.product_name or ""
                amount = float(item.amount or 0)
                weight = float(item.weight or 0)
                slot = prod_data.setdefault(name, {"amount": 0.0, "weight": 0.0})
                slot["amount"] += amount
                slot["weight"] += weight
                month_total_amount += amount
                month_total_weight += weight

        months.append({
            "month": month,
            "year": y,
            "key": f"{y}-{month:02d}",
            "label": f"{month:02d}/{y}",
            "month_total_amount": round(month_total_amount, 2),
            "month_total_weight": round(month_total_weight, 3),
            "products": prod_data,
        })

    return {
        "fy_start": fy_start,
        "fy_end": fy_end,
        "products": product_names,
        "product_ids": product_ids,
        "months": months,
    }


@router.get("/analysis/monthly-export")
def monthly_sales_export(
    fy_start: int | None = None,
    fy_end: int | None = None,
    db: Session = Depends(get_db),
):
    """Export monthly sales as CSV."""
    from backend.app.models import SalesChallan
    from datetime import date, timedelta

    now = _ist_wall()
    if not fy_start:
        fy_start = now.year if now.month >= 4 else now.year - 1
    if not fy_end:
        fy_end = fy_start + 1

    rows = []
    for m in range(4, 16):
        month = m if m <= 12 else m - 12
        year = fy_start if m <= 12 else fy_start + 1
        month_start = date(year, month, 1)
        month_end = date(year, month, 1) + timedelta(days=32)
        month_end = date(month_end.year, month_end.month, 1) - timedelta(days=1)

        challans = db.query(SalesChallan).filter(
            SalesChallan.challan_date >= month_start,
            SalesChallan.challan_date <= month_end,
            SalesChallan.is_archived == False,  # noqa: E712
        ).all()

        total_qty = 0
        total_amount = 0
        for ch in challans:
            for item in ch.items:
                total_qty += float(item.quantity or 0)
                total_amount += float(item.amount or 0)

        rows.append([f"{month:02d}/{year}", total_qty, total_amount])

    from fastapi.responses import PlainTextResponse
    import csv
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Month", "Quantity", "Amount"])
    writer.writerows(rows)
    return PlainTextResponse(output.getvalue(), media_type="text/csv")


def _invoice_to_dict(inv: SalesInvoice, db: Session = None) -> dict:
    """Convert a SalesInvoice ORM row to the dict that render_invoice_pdf
    and the invoice print view expect."""
    company = {}
    if db is not None:
        from backend.app.services.challan_snapshot import company_profile as _cp
        company = _cp(db)
    cust = inv.customer
    items = []
    for it in (inv.items or []):
        items.append({
            "product_name": it.product_name or "",
            "size_name": it.size_name or "",
            "hsn_code": it.hsn_code or "",
            "quantity": float(it.quantity or 0),
            "weight": float(it.weight or 0),
            "rate_per_kg": float(it.rate_per_kg or 0),
            "amount": float(it.amount or 0),
            "taxable_value": float(it.taxable_value or 0),
            "gst_rate": float(it.gst_rate or 0),
            "cgst": float(it.cgst or 0),
            "sgst": float(it.sgst or 0),
            "igst": float(it.igst or 0),
        })
    return {
        "invoice_number": inv.invoice_number or "",
        "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
        "challan_number": inv.challan.challan_number if inv.challan else "",
        "company_name": inv.company_name or company.get("name", ""),
        "company_address": inv.company_address or company.get("address", ""),
        "company_phone": inv.company_phone or company.get("phone", ""),
        "company_email": inv.company_email or company.get("email", ""),
        "company_gstin": inv.company_gstin or company.get("gstin", ""),
        "cust_company_name": inv.cust_company_name or (cust.company_name if cust else ""),
        "cust_address": inv.cust_address or (cust.address if cust else ""),
        "cust_gst_no": inv.cust_gst_no or (cust.gst_no if cust else ""),
        "cust_contact_number": inv.cust_contact_number or (cust.contact_number if cust else ""),
        "customer_email": cust.email if cust else "",
        "customer_name": cust.name if cust else "",
        "intra_state": bool(inv.intra_state),
        "place_of_supply": inv.place_of_supply or "",
        "items": items,
        "taxable_value": float(inv.taxable_value or 0),
        "cgst": float(inv.cgst or 0),
        "sgst": float(inv.sgst or 0),
        "igst": float(inv.igst or 0),
        "grand_total": float(inv.grand_total or 0),
        "total_in_words": "",
        "company_terms": inv.company_terms or company.get("terms", ""),
    }


@router.post("/invoices/{inv_id}/email")
def send_invoice_email(inv_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    """Email a sales invoice PDF to the customer.

    Defaults to the customer's email; explicit addresses are validated
    through the same recipient rules as _send_email. The PDF bytes go out
    as a real attachment with the invoice number in the filename.
    """
    inv = db.query(SalesInvoice).filter(SalesInvoice.id == inv_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")

    to_email = (payload or {}).get("to") or ""
    if not to_email:
        cust = db.query(Customer).filter(Customer.id == inv.customer_id).first()
        to_email = cust.email if cust else ""
    if not to_email:
        raise HTTPException(status_code=400, detail="No email address on customer or request")

    # Validate addresses using the same rule as _send_email
    parts = [p.strip() for p in re.split(r"[;,]", to_email) if p.strip()]
    for addr in parts:
        if "@" not in addr or "." not in addr.split("@")[-1]:
            raise HTTPException(status_code=400, detail=f"Bad email address: {addr}")

    pdf_bytes = render_invoice_pdf(_invoice_to_dict(inv, db))

    from backend.app.models import ApplicationSetting
    from email.mime.application import MIMEApplication
    from email.mime.multipart import MIMEMultipart

    def _get(key, default=None):
        row = db.query(ApplicationSetting).filter(ApplicationSetting.key == key).first()
        return row.value if row else default

    host = _get("email.smtp_host")
    if not host:
        raise HTTPException(status_code=400, detail="SMTP host not configured")
    username = _get("email.username")
    password = _get("email.password")
    port = int(_get("email.smtp_port", "587") or 587)
    use_tls = str(_get("email.use_tls", "true")).lower() != "false"
    from_addr = _get("email.from_addr", username or "")

    import smtplib
    from email.mime.text import MIMEText

    msg = MIMEMultipart()
    msg["Subject"] = f"Invoice {inv.invoice_number}"
    msg["From"] = from_addr
    msg["To"] = ", ".join(parts)
    msg.attach(MIMEText(f"Please find attached invoice {inv.invoice_number}."))
    part = MIMEApplication(pdf_bytes, _subtype="pdf")
    part.add_header("Content-Disposition", "attachment",
                    filename=f"{inv.invoice_number}.pdf")
    msg.attach(part)

    with smtplib.SMTP(host, port) as server:
        if use_tls:
            server.starttls()
        if username and password:
            server.login(username, password)
        server.sendmail(from_addr, parts, msg.as_string())

    return {"to": parts, "invoice_id": inv_id, "invoice_number": inv.invoice_number}


@router.get("/invoices/{inv_id}/pdf")
def download_invoice_pdf(inv_id: int, db: Session = Depends(get_db)):
    """Download a sales invoice as PDF."""
    inv = db.query(SalesInvoice).filter(SalesInvoice.id == inv_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    pdf_bytes = render_invoice_pdf(_invoice_to_dict(inv, db))
    import io
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{inv.invoice_number}.pdf"'},
    )