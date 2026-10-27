"""Customer routes"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import Customer, CustomerPayment, SalesChallan
from backend.app.schemas import CustomerCreate, CustomerSchema, CustomerUpdate
from backend.app.core.shared import (
    _audit,
    _change_reason,
    _current_user,
    _reject_if_archived,
    _archive_record,
    _restore_record,
    _request_user_and_role,
)

router = APIRouter()


@router.get("/customers", response_model=list[CustomerSchema])
def list_customers(db: Session = Depends(get_db)):
    return db.query(Customer).filter(Customer.is_active == True).order_by(Customer.name).all()  # noqa: E712


@router.post("/customers", response_model=CustomerSchema)
def create_customer(customer: CustomerCreate, request: Request, db: Session = Depends(get_db)):
    exists = db.query(Customer).filter(Customer.name == customer.name).first()
    if exists:
        raise HTTPException(status_code=400, detail="Customer with this name already exists")
    c = Customer(name=customer.name, contact_number=customer.contact_number, address=customer.address, gstin=customer.gstin, email=customer.email)
    db.add(c)
    _audit(db, request, "customers", "create", record=c, record_type="customer", new_values={"name": c.name, "contact_number": c.contact_number})
    db.commit()
    db.refresh(c)
    return c


@router.get("/customers/{customer_id}", response_model=CustomerSchema)
def get_customer(customer_id: int, db: Session = Depends(get_db)):
    c = db.query(Customer).filter(Customer.id == customer_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")
    return c


@router.get("/customers/{customer_id}/statement")
def customer_statement(customer_id: int, db: Session = Depends(get_db)):
    c = db.query(Customer).filter(Customer.id == customer_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")
    challans = db.query(SalesChallan).filter(SalesChallan.customer_id == customer_id, SalesChallan.is_archived == False).all()  # noqa: E712
    # CustomerPayment has no customer_id column; it links via challan_id, so
    # join through SalesChallan to find payments for this customer's challans.
    payments = (
        db.query(CustomerPayment)
        .join(SalesChallan, CustomerPayment.challan_id == SalesChallan.id)
        .filter(SalesChallan.customer_id == customer_id, SalesChallan.is_archived == False)
        .all()
    )
    from backend.app.services import challan_snapshot as _snap
    balances = _snap.all_challan_balances(db)
    items = []
    for ch in challans:
        bal = balances.get(ch.id, 0)
        items.append({
            "type": "challan",
            "id": ch.id,
            "date": ch.challan_date.isoformat() if ch.challan_date else None,
            "challan_number": ch.challan_number,
            "total_amount": float(ch.total_price or 0),
            "balance": float(bal),
        })
    for p in payments:
        items.append({
            "type": "payment",
            "id": p.id,
            "date": p.payment_date.isoformat() if p.payment_date else None,
            "challan_id": p.challan_id,
            "amount": float(p.amount or 0),
            "mode": p.payment_mode,
        })
    items.sort(key=lambda x: x["date"] or "", reverse=True)
    total_due = sum(i["balance"] for i in items if i["type"] == "challan" and i["balance"] > 0)
    return {"customer": {"id": c.id, "name": c.name}, "items": items, "total_due": float(total_due)}


@router.put("/customers/{customer_id}", response_model=CustomerSchema)
def update_customer(customer_id: int, update: CustomerUpdate, request: Request, db: Session = Depends(get_db)):
    c = db.query(Customer).filter(Customer.id == customer_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")
    _reject_if_archived(c, "customer")
    reason = _change_reason(request)
    old = {"name": c.name, "contact_number": c.contact_number, "address": c.address, "gstin": c.gstin, "email": c.email}
    data = update.dict(exclude_unset=True)
    for field, value in data.items():
        if value is not None:
            setattr(c, field, value)
    _audit(db, request, "customers", "update", record=c, record_type="customer", old_values=old, new_values={"name": c.name, "contact_number": c.contact_number, "address": c.address, "gstin": c.gstin, "email": c.email}, notes=reason)
    db.commit()
    db.refresh(c)
    return c


@router.delete("/customers/{customer_id}")
def delete_customer(customer_id: int, request: Request, db: Session = Depends(get_db)):
    c = db.query(Customer).filter(Customer.id == customer_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")
    reason = _change_reason(request)
    if not _archive_record(db, request, c, "customers", "customer"):
        raise HTTPException(status_code=409, detail="Customer already archived")
    _audit(db, request, "customers", "archive", record=c, record_type="customer", old_values={"is_active": True, "is_archived": False}, new_values={"is_active": False, "is_archived": True}, notes=reason)
    db.commit()
    return {"status": "archived", "id": customer_id}


@router.post("/customers/{customer_id}/restore")
def restore_customer(customer_id: int, request: Request, db: Session = Depends(get_db)):
    c = db.query(Customer).filter(Customer.id == customer_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")
    reason = _change_reason(request)
    if not _restore_record(db, request, c, "customers", "customer"):
        raise HTTPException(status_code=409, detail="Customer is not archived")
    _audit(db, request, "customers", "restore", record=c, record_type="customer", old_values={"is_archived": True}, new_values={"is_archived": False}, notes=reason)
    db.commit()
    return {"status": "restored", "id": customer_id}


# Cash movements
@router.get("/customers/{customer_id}/cash")
def customer_cash_movements(customer_id: int, db: Session = Depends(get_db)):
    c = db.query(Customer).filter(Customer.id == customer_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")
    from backend.app.models import CashMovement
    movements = db.query(CashMovement).filter(CashMovement.party_type == "customer", CashMovement.party_id == customer_id).order_by(CashMovement.id.desc()).all()
    return [{"id": m.id, "date": m.movement_date.isoformat() if m.movement_date else None, "type": m.movement_type, "amount": float(m.amount or 0), "note": m.note, "voided": m.voided} for m in movements]


@router.post("/customers/{customer_id}/cash")
def add_customer_cash(customer_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    c = db.query(Customer).filter(Customer.id == customer_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")
    from backend.app.models import CashMovement
    m = CashMovement(party_type="customer", party_id=customer_id, date=payload.get("date"), type=payload.get("type"), amount=payload.get("amount"), note=payload.get("note"))
    db.add(m)
    _audit(db, request, "customers", "cash_create", record=m, record_type="cash_movement", new_values={"party": c.name, "type": m.movement_type, "amount": float(m.amount or 0)})
    db.commit()
    db.refresh(m)
    return {"id": m.id, "status": "created"}


@router.post("/customers/{customer_id}/cash/{movement_id}/void")
def void_customer_cash(customer_id: int, movement_id: int, request: Request, db: Session = Depends(get_db)):
    from backend.app.models import CashMovement
    m = db.query(CashMovement).filter(CashMovement.id == movement_id, CashMovement.party_type == "customer", CashMovement.party_id == customer_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Movement not found")
    if m.voided:
        raise HTTPException(status_code=409, detail="Already voided")
    m.voided = True
    m.voided_at = _ist_wall()
    _audit(db, request, "customers", "cash_void", record=m, record_type="cash_movement", old_values={"voided": False}, new_values={"voided": True})
    db.commit()
    return {"status": "voided", "id": movement_id}