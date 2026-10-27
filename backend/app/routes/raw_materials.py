"""Raw material routes"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import RawMaterial, RawMaterialCategory
from backend.app.schemas import RawMaterialCreate, RawMaterialSchema, RawMaterialUpdate
from backend.app.core.shared import (
    _audit,
    _change_reason,
    _reject_if_archived,
    _archive_record,
    _restore_record,
    _ist_wall,
    _request_user_and_role,
)

router = APIRouter()


@router.get("/raw-materials", response_model=list[RawMaterialSchema])
def list_raw_materials(db: Session = Depends(get_db)):
    return db.query(RawMaterial).filter(RawMaterial.is_active == True).order_by(RawMaterial.name).all()  # noqa: E712


@router.post("/raw-materials", response_model=RawMaterialSchema)
def create_raw_material(material: RawMaterialCreate, request: Request, db: Session = Depends(get_db)):
    exists = db.query(RawMaterial).filter(RawMaterial.name == material.name).first()
    if exists:
        raise HTTPException(status_code=400, detail="Raw material with this name already exists")
    m = RawMaterial(
        name=material.name,
        category_id=material.category_id,
        unit=material.unit,
        current_stock=material.current_stock or 0,
        threshold_stock=material.threshold_stock or 0,
        watch_stock=material.watch_stock if material.watch_stock is not None else True,
        hsn_code=material.hsn_code,
    )
    db.add(m)
    _audit(db, request, "raw_materials", "create", record=m, record_type="raw_material", new_values={"name": m.name, "unit": m.unit})
    db.commit()
    db.refresh(m)
    return m


@router.get("/raw-materials/{material_id}", response_model=RawMaterialSchema)
def get_raw_material(material_id: int, db: Session = Depends(get_db)):
    m = db.query(RawMaterial).filter(RawMaterial.id == material_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Raw material not found")
    return m


@router.put("/raw-materials/{material_id}", response_model=RawMaterialSchema)
def update_raw_material(material_id: int, update: RawMaterialUpdate, request: Request, db: Session = Depends(get_db)):
    m = db.query(RawMaterial).filter(RawMaterial.id == material_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Raw material not found")
    _reject_if_archived(m, "raw material")
    reason = _change_reason(request)
    old = {"name": m.name, "category_id": m.category_id, "unit": m.unit, "current_stock": m.current_stock, "threshold_stock": m.threshold_stock, "watch_stock": m.watch_stock, "hsn_code": m.hsn_code}
    data = update.dict(exclude_unset=True)
    for field, value in data.items():
        if value is not None:
            setattr(m, field, value)
    _audit(db, request, "raw_materials", "update", record=m, record_type="raw_material", old_values=old, new_values={"name": m.name, "category_id": m.category_id, "unit": m.unit, "current_stock": m.current_stock, "threshold_stock": m.threshold_stock, "watch_stock": m.watch_stock, "hsn_code": m.hsn_code}, notes=reason)
    db.commit()
    db.refresh(m)
    return m


@router.delete("/raw-materials/{material_id}")
def delete_raw_material(material_id: int, request: Request, db: Session = Depends(get_db)):
    m = db.query(RawMaterial).filter(RawMaterial.id == material_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Raw material not found")
    reason = _change_reason(request)
    if not _archive_record(db, request, m, "raw_materials", "raw_material"):
        raise HTTPException(status_code=409, detail="Raw material already archived")
    _audit(db, request, "raw_materials", "archive", record=m, record_type="raw_material", old_values={"is_active": True, "is_archived": False}, new_values={"is_active": False, "is_archived": True}, notes=reason)
    db.commit()
    return {"status": "archived", "id": material_id}


@router.post("/raw-materials/{material_id}/restore")
def restore_raw_material(material_id: int, request: Request, db: Session = Depends(get_db)):
    m = db.query(RawMaterial).filter(RawMaterial.id == material_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Raw material not found")
    reason = _change_reason(request)
    if not _restore_record(db, request, m, "raw_materials", "raw_material"):
        raise HTTPException(status_code=409, detail="Raw material is not archived")
    _audit(db, request, "raw_materials", "restore", record=m, record_type="raw_material", old_values={"is_archived": True}, new_values={"is_archived": False}, notes=reason)
    db.commit()
    return {"status": "restored", "id": material_id}


# Raw material categories
@router.get("/raw-material-categories")
def list_raw_material_categories(db: Session = Depends(get_db)):
    return db.query(RawMaterialCategory).order_by(RawMaterialCategory.name).all()


@router.post("/raw-material-categories")
def create_raw_material_category(payload: dict, request: Request, db: Session = Depends(get_db)):
    name = (payload or {}).get("name")
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    if db.query(RawMaterialCategory).filter(RawMaterialCategory.name == name).first():
        raise HTTPException(status_code=400, detail="Category already exists")
    cat = RawMaterialCategory(name=name)
    db.add(cat)
    _audit(db, request, "raw_materials", "category_create", record=cat, record_type="raw_material_category", new_values={"name": name})
    db.commit()
    db.refresh(cat)
    return cat