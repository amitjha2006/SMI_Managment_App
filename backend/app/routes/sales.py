"""Sales challan, finished stock, stock adjustments, analysis routes"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, date
from decimal import Decimal as D

from backend.app.core.database import get_db
from backend.app.models import (
    Customer,
    CustomerPayment,
    FinishedProductStockMovement,
    PackagingSize,
    Product,
    RawMaterial,
    RawMaterialStockMovement,
    SalesChallan,
    SalesChallanItem,
    SalesInvoice,
    SalesInvoiceItem,
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
    _request_user_and_role,
)
from backend.app.services import challan_snapshot as _snap
from backend.app.services.stock_posting import challan_stock_posted, post_challan_sales_movements

router = APIRouter()


# Challan routes
@router.get("/challans/dispatch-options")
def dispatch_options(db: Session = Depends(get_db)):
    """Challans eligible for dispatch (confirmed, not dispatched, not archived)."""
    challans = db.query(SalesChallan).filter(
        SalesChallan.status == ChallanStatus.CONFIRMED,
        SalesChallan.dispatched == False,  # noqa: E712
        SalesChallan.is_archived == False,  # noqa: E712
    ).order_by(SalesChallan.challan_date.desc()).all()
    return [
        {
            "id": c.id,
            "challan_number": c.challan_number,
            "customer_id": c.customer_id,
            "customer_name": c.customer.name if c.customer else None,
            "date": c.challan_date.isoformat() if c.challan_date else None,
            "total_amount": float(c.total_price or 0),
        }
        for c in challans
    ]


@router.get("/challans", response_model=list[SalesChallanSchema])
def list_challans(request: Request = None, db: Session = Depends(get_db)):
    from backend.app.core.shared import _challan_entry_allowed, _latest_login_at
    from backend.main import _current_user
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
    return query.all()


@router.get("/challans/{challan_id}", response_model=SalesChallanSchema)
def get_challan(challan_id: int, db: Session = Depends(get_db)):
    c = db.query(SalesChallan).filter(SalesChallan.id == challan_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Challan not found")
    return c


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
    reason = _change_reason(request)

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
    reason = _change_reason(request)
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
    reason = _change_reason(request)
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
    reason = _change_reason(request)

    new_status = ChallanStatus(payload.get("status"))
    old_status = c.status

    if new_status == ChallanStatus.CONFIRMED and old_status != ChallanStatus.CONFIRMED:
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
    return c


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
    if ch.status != ChallanStatus.CONFIRMED:
        raise HTTPException(status_code=400, detail="Challan must be confirmed first")
    
    # Check if invoice already exists
    existing = db.query(SalesInvoice).filter(SalesInvoice.challan_id == challan_id).first()
    if existing:
        raise HTTPException(status_code=409, detail="Invoice already exists for this challan")
    
    # Generate invoice number
    from backend.app.services.document_sequence import next_sales_invoice_number
    invoice_number = next_sales_invoice_number(db)
    
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
            sales_invoice_id=inv.id,
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
    if ch.outstanding_amount == D("0"):
        ch.status = ChallanStatus.PAID
    elif ch.outstanding_amount < ch.total_price:
        ch.status = ChallanStatus.PARTIALLY_PAID
    
    _audit(db, request, "sales", "payment_create", record=pay, record_type="customer_payment",
           new_values={"challan_id": ch.id, "amount": float(amount)})
    db.commit()
    db.refresh(pay)
    return {"amount": float(pay.amount), "id": pay.id, "challan_id": pay.challan_id, "payment_date": pay.payment_date.isoformat() if pay.payment_date else None, "payment_mode": pay.payment_mode}


# Finished stock
@router.get("/finished-stock")
def list_finished_stock(db: Session = Depends(get_db)):
    """Per-product finished stock totals (kg).

    Reconciles with /finished-stock/balance: the balance rows' totals must
    sum to these product totals (guard test: test_agrees_with_the_existing_
    per_product_endpoint).
    """
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
        if r.movement_type in (FinishedProductMovementType.PRODUCTION, FinishedProductMovementType.OPENING):
            delta = q
        elif r.movement_type in (
            FinishedProductMovementType.SALES,
            FinishedProductMovementType.ADJUSTMENT,
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
        if r.movement_type in (FinishedProductMovementType.PRODUCTION, FinishedProductMovementType.OPENING):
            slot["stock_kg"] += q
        elif r.movement_type == FinishedProductMovementType.SALES:
            slot["sold_kg"] += q
            slot["out_kg"] += q
        elif r.movement_type in (
            FinishedProductMovementType.ADJUSTMENT,
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
def stock_adjustment_reasons():
    return [
        "damage",
        "theft",
        "expired",
        "quality_reject",
        "measurement_error",
        "other",
    ]


@router.get("/stock-adjustments")
def list_stock_adjustments(db: Session = Depends(get_db)):
    """Unified adjustment history (finished goods + raw materials), newest first.

    One list feeds both the Inventory and Raw Materials pages; each row is
    tagged kind ("Finished Goods"/"Raw Material") and pages filter on it.
    """
    from datetime import datetime

    fg_sizes = {s.id: s.size_name for s in db.query(PackagingSize).all()}
    rows = []
    for m in (
        db.query(FinishedProductStockMovement)
        .filter(FinishedProductStockMovement.movement_type == FinishedProductMovementType.ADJUSTMENT)
        .all()
    ):
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
    for m in (
        db.query(RawMaterialStockMovement)
        .filter(RawMaterialStockMovement.movement_type == "adjustment")
        .all()
    ):
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
    from_date: str | None = None,
    to_date: str | None = None,
    type: str | None = None,
    db: Session = Depends(get_db),
):
    """Transaction analysis (purchases, sales, production)."""
    from datetime import datetime
    results = []

    # Purchase invoices
    if not type or type == "purchase":
        q = db.query(PurchaseInvoice).filter(PurchaseInvoice.is_archived == False)  # noqa: E712
        if from_date:
            q = q.filter(PurchaseInvoice.invoice_date >= datetime.fromisoformat(from_date).date())
        if to_date:
            q = q.filter(PurchaseInvoice.invoice_date <= datetime.fromisoformat(to_date).date())
        for inv in q.all():
            results.append({
                "type": "purchase",
                "id": inv.id,
                "number": inv.invoice_number,
                "date": inv.invoice_date.isoformat() if inv.invoice_date else None,
                "party": inv.supplier.name if inv.supplier else None,
                "amount": float(inv.total_amount or 0),
            })

    # Sales challans
    if not type or type == "sale":
        q = db.query(SalesChallan).filter(SalesChallan.is_archived == False)  # noqa: E712
        if from_date:
            q = q.filter(SalesChallan.challan_date >= datetime.fromisoformat(from_date).date())
        if to_date:
            q = q.filter(SalesChallan.challan_date <= datetime.fromisoformat(to_date).date())
        for ch in q.all():
            results.append({
                "type": "sale",
                "id": ch.id,
                "number": ch.challan_number,
                "date": ch.challan_date.isoformat() if ch.challan_date else None,
                "party": ch.customer.name if ch.customer else None,
                "amount": float(ch.total_price or 0),
            })

    # Production entries
    if not type or type == "production":
        from backend.app.models import ProductionEntry
        q = db.query(ProductionEntry).filter(ProductionEntry.is_archived == False)  # noqa: E712
        if from_date:
            q = q.filter(ProductionEntry.created_at >= datetime.fromisoformat(from_date))
        if to_date:
            q = q.filter(ProductionEntry.created_at <= datetime.fromisoformat(to_date) + timedelta(days=1))
        for e in q.all():
            results.append({
                "type": "production",
                "id": e.id,
                "number": e.production_number,
                "date": e.created_at.isoformat() if e.created_at else None,
                "product": e.product.name if e.product else None,
                "amount": 0,
            })

    results.sort(key=lambda x: x["date"] or "", reverse=True)
    return results


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