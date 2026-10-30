"""Supplier routes"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import Supplier, SupplierPayment, PurchaseInvoice
from backend.app.schemas import SupplierCreate, SupplierSchema, SupplierUpdate
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

router = APIRouter()


@router.get("/suppliers", response_model=list[SupplierSchema])
def list_suppliers(db: Session = Depends(get_db)):
    return db.query(Supplier).filter(Supplier.is_active == True).order_by(Supplier.name).all()  # noqa: E712


@router.post("/suppliers", response_model=SupplierSchema)
def create_supplier(supplier: SupplierCreate, request: Request, db: Session = Depends(get_db)):
    exists = db.query(Supplier).filter(Supplier.name == supplier.name).first()
    if exists:
        raise HTTPException(status_code=400, detail="Supplier with this name already exists")
    s = Supplier(name=supplier.name, contact_number=supplier.contact_number, address=supplier.address, gstin=supplier.gstin, email=supplier.email)
    db.add(s)
    _audit(db, request, "suppliers", "create", record=s, record_type="supplier", new_values={"name": s.name, "contact_number": s.contact_number})
    db.commit()
    db.refresh(s)
    return s


@router.get("/suppliers/{supplier_id}", response_model=SupplierSchema)
def get_supplier(supplier_id: int, db: Session = Depends(get_db)):
    s = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return s


@router.put("/suppliers/{supplier_id}", response_model=SupplierSchema)
def update_supplier(supplier_id: int, update: SupplierUpdate, request: Request, db: Session = Depends(get_db)):
    s = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Supplier not found")
    _reject_if_archived(s, "supplier")
    reason = ""
    old = {"name": s.name, "contact_number": s.contact_number, "address": s.address, "gstin": s.gstin, "email": s.email}
    data = update.dict(exclude_unset=True)
    for field, value in data.items():
        if value is not None:
            setattr(s, field, value)
    _audit(db, request, "suppliers", "update", record=s, record_type="supplier", old_values=old, new_values={"name": s.name, "contact_number": s.contact_number, "address": s.address, "gstin": s.gstin, "email": s.email}, notes=reason)
    db.commit()
    db.refresh(s)
    return s


@router.delete("/suppliers/{supplier_id}")
def delete_supplier(supplier_id: int, request: Request, db: Session = Depends(get_db)):
    s = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Supplier not found")
    reason = ""
    if not _archive_record(db, request, s, "suppliers", "supplier"):
        raise HTTPException(status_code=409, detail="Supplier already archived")
    _audit(db, request, "suppliers", "archive", record=s, record_type="supplier", old_values={"is_active": True, "is_archived": False}, new_values={"is_active": False, "is_archived": True}, notes=reason)
    db.commit()
    return {"status": "archived", "id": supplier_id}


@router.post("/suppliers/{supplier_id}/restore")
def restore_supplier(supplier_id: int, request: Request, db: Session = Depends(get_db)):
    s = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Supplier not found")
    reason = ""
    if not _restore_record(db, request, s, "suppliers", "supplier"):
        raise HTTPException(status_code=409, detail="Supplier is not archived")
    _audit(db, request, "suppliers", "restore", record=s, record_type="supplier", old_values={"is_archived": True}, new_values={"is_archived": False}, notes=reason)
    db.commit()
    return {"status": "restored", "id": supplier_id}


# Cash movements
@router.post("/suppliers/{supplier_id}/cash")
def supplier_cash_movements(supplier_id: int, db: Session = Depends(get_db)):
    s = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Supplier not found")
    from backend.app.models import CashMovement
    movements = db.query(CashMovement).filter(CashMovement.party_type == "supplier", CashMovement.party_id == supplier_id).order_by(CashMovement.id.desc()).all()
    return [{"id": m.id, "date": m.movement_date.isoformat() if m.movement_date else None, "type": m.movement_type, "amount": float(m.amount or 0), "note": m.note, "voided": m.voided} for m in movements]


@router.post("/suppliers/{supplier_id}/cash")
def add_supplier_cash(supplier_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    s = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Supplier not found")
    from backend.app.models import CashMovement
    m = CashMovement(party_type="supplier", party_id=supplier_id, date=payload.get("date"), type=payload.get("type"), amount=payload.get("amount"), note=payload.get("note"))
    db.add(m)
    _audit(db, request, "suppliers", "cash_create", record=m, record_type="cash_movement", new_values={"party": s.name, "type": m.movement_type, "amount": float(m.amount or 0)})
    db.commit()
    db.refresh(m)
    return {"id": m.id, "status": "created"}


@router.post("/suppliers/{supplier_id}/cash/{movement_id}/void")
def void_supplier_cash(supplier_id: int, movement_id: int, request: Request, db: Session = Depends(get_db)):
    from backend.app.models import CashMovement
    m = db.query(CashMovement).filter(CashMovement.id == movement_id, CashMovement.party_type == "supplier", CashMovement.party_id == supplier_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Movement not found")
    if m.voided:
        raise HTTPException(status_code=409, detail="Already voided")
    m.voided = True
    m.voided_at = _ist_wall()
    reason = _change_reason(request)
    _audit(db, request, "suppliers", "cash_void", record=m, record_type="cash_movement", old_values={"voided": False}, new_values={"voided": True}, notes=reason)
    db.commit()
    return {"status": "voided", "id": movement_id}