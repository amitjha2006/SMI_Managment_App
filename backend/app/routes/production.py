"""Production entry routes"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session
from datetime import date, datetime, timedelta

from backend.app.core.database import get_db
from backend.app.models.finished_product import (
    FinishedProductMovementType,
    FinishedProductStockMovement,
)
from backend.app.models import (
    PackagingSize,
    Product,
    ProductionEntry,
    ProductionOutput,
    ProductionStageEntry,
    RawMaterial,
    RawMaterialStockMovement,
)
from backend.app.schemas import ProductionEntrySchema, ProductionStage, ProductionStatus
from backend.app.core.shared import (
    _audit,
    _change_reason,
    _current_user,
    _ist_wall,
    _reject_if_archived,
    _archive_record,
    _restore_record,
    _request_user_and_role,
)
from backend.app.services import production_workflow as _wf
from backend.app.services.stock_posting import challan_stock_posted, post_challan_sales_movements

router = APIRouter()


@router.get("/production/entries", response_model=list[ProductionEntrySchema])
def list_production_entries(db: Session = Depends(get_db)):
    return db.query(ProductionEntry).filter(ProductionEntry.is_archived == False).order_by(ProductionEntry.id.desc()).all()  # noqa: E712


@router.get("/production/entries/{entry_id}", response_model=ProductionEntrySchema)
def get_production_entry(entry_id: int, db: Session = Depends(get_db)):
    e = db.query(ProductionEntry).filter(ProductionEntry.id == entry_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Production entry not found")
    return e


def _dec(value):
    from decimal import Decimal
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _entry_dict(e):
    """Serialise an entry to a dict (tests and routes both consume it).

    stage_entries/outputs stay as ORM rows so callers can read typed
    columns (Decimal quantities) off them directly.
    """
    return {
        "id": e.id,
        "production_number": e.production_number,
        "production_date": e.production_date,
        "product_id": e.product_id,
        "packaging_size_id": e.packaging_size_id,
        "target_quantity": e.target_quantity,
        "actual_output": e.actual_output,
        "packed_at": e.packed_at,
        "status": e.status.value if e.status else None,
        "notes": e.notes,
        "created_at": e.created_at,
        "created_by": e.created_by,
        "is_archived": e.is_archived,
        "completed_at": getattr(e, "completed_at", None),
        "product_name": e.product.name if e.product else None,
        "stage_entries": e.stage_entries or [],
        "outputs": e.outputs or [],
    }


def _post_raw_consumption(db, raw_material_id, qty_kg, batch_date, production_id, production_number, note=None):
    """Deduct raw stock and post one consumption movement (one writer)."""
    rm = db.query(RawMaterial).filter(RawMaterial.id == raw_material_id).first()
    if not rm:
        return
    rm.current_stock = (rm.current_stock or 0) - _dec(qty_kg)
    db.add(RawMaterialStockMovement(
        raw_material_id=raw_material_id,
        movement_type="consumption",
        quantity=_dec(qty_kg),
        unit=rm.unit or "kg",
        reference_type="production",
        reference_id=production_id,
        notes=note or f"Consumed for production {production_number}",
        movement_date=batch_date,
    ))


def _add_stage_row(db, entry_id, stage_payload, sequence):
    """Build one ProductionStageEntry from a dict, computing wastage/gain.

    Stage edits are process records only: they never move stock.
    """
    from backend.app.services import production_wastage as _pw
    name = str(stage_payload.get("stage") or "").strip().lower()
    try:
        stage_enum = ProductionStage(name)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown production stage '{name}'")
    loss = _pw.stage_loss(
        stage_payload.get("input_quantity", 0),
        stage_payload.get("output_quantity", 0),
        is_packaging=(name == "packaging"),
    )
    return ProductionStageEntry(
        production_entry_id=entry_id,
        stage=stage_enum,
        sequence=sequence,
        input_quantity=loss.input_kg,
        output_quantity=loss.output_kg,
        wastage_quantity=loss.wastage_kg,
        wastage_percentage=loss.wastage_pct,
        packing_gain_kg=loss.packing_gain_kg,
        notes=stage_payload.get("notes"),
    )


@router.post("/production/entry", response_model=ProductionEntrySchema)
def create_production_entry(entry: dict, request: Request, db: Session = Depends(get_db)):
    entry = entry or {}
    # Validate product exists
    product = db.query(Product).filter(Product.id == entry.get("product_id")).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # Generate production number
    from backend.app.services.document_sequence import next_production_number
    production_number = next_production_number(db)

    production_date = entry.get("production_date")
    if isinstance(production_date, str):
        production_date = datetime.fromisoformat(production_date)

    # Calculate actual_output from outputs if not provided
    outputs_data = entry.get("outputs") or []
    if outputs_data and not entry.get("actual_output"):
        actual_output = _dec(0)
        for out in outputs_data:
            qty = _dec(out.get("quantity_kg", out.get("weight_kg", 0)))
            # Outputs often arrive as packets only; convert via the size's
            # weight_in_kg so actual_output reflects the packed batch.
            if not qty and out.get("packets"):
                size = (
                    db.query(PackagingSize)
                    .filter(PackagingSize.id == out.get("packaging_size_id"))
                    .first()
                )
                if size and size.weight_in_kg:
                    qty = _dec(out.get("packets")) * size.weight_in_kg
            actual_output += qty
    else:
        actual_output = _dec(entry.get("actual_output"))

    # Create entry
    e = ProductionEntry(
        production_number=production_number,
        production_date=production_date or _ist_wall(),
        product_id=entry.get("product_id"),
        target_quantity=_dec(entry.get("target_quantity")),
        actual_output=actual_output,
        notes=entry.get("notes"),
        status=ProductionStatus.IN_PROGRESS,
        created_by=_current_user(db, request).id if _current_user(db, request) else None,
    )
    db.add(e)
    db.flush()

    # Add consumed materials: deduct stock + post a dated movement at save
    from backend.app.models import ProductionConsumedMaterial
    for mat in entry.get("consumed_materials") or []:
        qty_kg = _dec(mat.get("quantity", mat.get("quantity_kg", 0)))
        db.add(ProductionConsumedMaterial(
            production_entry_id=e.id,
            raw_material_id=mat.get("raw_material_id"),
            quantity_kg=qty_kg,
        ))
        _post_raw_consumption(db, mat.get("raw_material_id"), qty_kg,
                              production_date or _ist_wall(), e.id, production_number)

    # Add stages (with computed wastage/packing gain)
    for seq, stage in enumerate(entry.get("stages") or [], start=1):
        db.add(_add_stage_row(db, e.id, stage, seq))

    # Add unpacked outputs (rare; the normal flow packs later)
    for out in entry.get("outputs") or []:
        db.add(ProductionOutput(
            production_entry_id=e.id,
            packaging_size_id=out.get("packaging_size_id"),
            packets=_dec(out.get("packets", 0)),
            quantity_kg=_dec(out.get("quantity_kg", out.get("weight_kg", 0))),
        ))

    _audit(db, request, "production", "create", record=e, record_type="production_entry", new_values={"production_number": production_number, "product_id": entry.get("product_id")})
    db.commit()
    db.refresh(e)
    return _entry_dict(e)


@router.post("/production/entries/{entry_id}/pack", response_model=ProductionEntrySchema)
def pack_production_entry(entry_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    payload = payload or {}
    e = db.query(ProductionEntry).filter(ProductionEntry.id == entry_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Production entry not found")
    _reject_if_archived(e, "production entry")
    if e.packed_at:
        raise HTTPException(status_code=400, detail="Entry already packed")

    outputs = payload.get("outputs") or []
    if not outputs:
        raise HTTPException(status_code=400, detail="Add at least one pack size before packing")

    packed_date = payload.get("packed_date")
    if isinstance(packed_date, str):
        packed_date = datetime.fromisoformat(packed_date)
    if not packed_date:
        packed_date = _ist_wall()

    # Bulk kg that went into packing: the packed-before stage output.
    bulk_kg = _dec(e.actual_output)

    # Rewrite the pack-size split
    db.query(ProductionOutput).filter(ProductionOutput.production_entry_id == entry_id).delete()
    wrapper_kg = _dec(0)
    finished_kg = _dec(0)
    for out in outputs:
        size = db.query(PackagingSize).filter(PackagingSize.id == out.get("packaging_size_id")).first()
        if not size:
            raise HTTPException(status_code=400, detail="Unknown packaging size")
        packets = _dec(out.get("packets", 0))
        kg = packets * _dec(size.weight_in_kg)
        finished_kg += kg
        db.add(ProductionOutput(
            production_entry_id=entry_id,
            packaging_size_id=size.id,
            packets=packets,
            quantity_kg=kg,
        ))
        # Wrapper: consumes the mapped roll at the packing date
        if size.wrapper_material_id and size.wrapper_weight_kg:
            wrapper_kg += packets * _dec(size.wrapper_weight_kg)
            _post_raw_consumption(
                db, size.wrapper_material_id,
                packets * _dec(size.wrapper_weight_kg),
                packed_date, e.id, e.production_number,
                note=f"Wrapper for {packets} packets of {size.size_name}",
            )
        # Post finished stock at the packing date
        db.add(FinishedProductStockMovement(
            product_id=e.product_id,
            packaging_size_id=size.id,
            movement_type=FinishedProductMovementType.PRODUCTION,
            reference_type="production",
            reference_id=e.id,
            quantity=kg,
            packets=int(packets),
            notes=f"Produced {packets} packets of {size.size_name}",
            movement_date=packed_date,
        ))
    e.actual_output = finished_kg
    e.packed_at = packed_date
    e.packaging_size_id = outputs[0].get("packaging_size_id")
    e.status = ProductionStatus.COMPLETED

    from backend.app.services import production_wastage as _pw
    # PACKAGING stage row: input = bulk kg in, output adds wrapper weight; the
    # surplus is packing gain, not wastage.
    loss = _pw.stage_loss(bulk_kg, bulk_kg + wrapper_kg, is_packaging=True)

    # Packaging stage row: always add so pack history is visible
    max_seq = db.query(func.max(ProductionStageEntry.sequence)).filter(
        ProductionStageEntry.production_entry_id == entry_id).scalar() or 0
    db.add(ProductionStageEntry(
        production_entry_id=entry_id,
        stage=ProductionStage.PACKAGING,
        sequence=max_seq + 1,
        input_quantity=loss.input_kg,
        output_quantity=loss.output_kg,
        wastage_quantity=loss.wastage_kg,
        wastage_percentage=loss.wastage_pct,
        packing_gain_kg=loss.packing_gain_kg,
    ))

    _audit(db, request, "production", "pack", record=e, record_type="production_entry", new_values={"output_count": len(outputs)})
    db.commit()
    db.refresh(e)
    return _entry_dict(e)


@router.put("/production/entries/{entry_id}/status", response_model=ProductionEntrySchema)
def update_production_status(entry_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    return set_production_status(entry_id, payload, db, request=request)


def set_production_status(entry_id: int, payload: dict, db: Session, request: Request = None):
    """Change a production entry's status. Never moves stock: consumption
    posts at save, wrapper + finished stock post at pack. Transitions:
    cancelled -> draft allowed (reopen), completed -> draft refused,
    draft -> completed refused until packed."""
    e = db.query(ProductionEntry).filter(ProductionEntry.id == entry_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Production entry not found")
    if e.is_archived:
        raise HTTPException(status_code=409, detail="Production entry is archived")

    raw = (payload or {}).get("status")
    try:
        new_status = ProductionStatus(str(raw))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown production status '{raw}'")

    old_status = e.status
    if new_status == ProductionStatus.DRAFT and old_status == ProductionStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="A completed batch cannot be reopened as draft")
    if new_status == ProductionStatus.COMPLETED and not e.packed_at:
        has_outputs = (
            db.query(ProductionOutput)
            .filter(ProductionOutput.production_entry_id == entry_id)
            .count() > 0
        )
        if not has_outputs:
            raise HTTPException(status_code=400, detail="Batch not packed yet")

    e.status = new_status
    _audit(db, request, "production", "status_change", record=e, record_type="production_entry",
           old_values={"status": old_status.value if old_status else None},
           new_values={"status": new_status.value})
    db.commit()
    db.refresh(e)
    return _entry_dict(e)


@router.put("/production/entries/{entry_id}/stages", response_model=ProductionEntrySchema)
def update_production_stages(entry_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    e = db.query(ProductionEntry).filter(ProductionEntry.id == entry_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Production entry not found")
    _reject_if_archived(e, "production entry")
    # Stage edits are process records; a hard-mandatory note lives at the
    # routes that change money/stock. Capture what's here, don't raise.
    try:
        _qp = getattr(request, "query_params", None)
        reason = (_qp.get("reason") or "").strip() if _qp is not None else ""
    except Exception:
        reason = ""

    stages = (payload or {}).get("stages") or []
    # Delete existing stages then rebuild; no stock ever moves here
    db.query(ProductionStageEntry).filter(ProductionStageEntry.production_entry_id == entry_id).delete()
    for seq, stage in enumerate(stages, start=1):
        db.add(_add_stage_row(db, entry_id, stage, seq))

    _audit(db, request, "production", "stages_update", record=e, record_type="production_entry", new_values={"stage_count": len(stages)}, notes=reason)
    db.commit()
    db.refresh(e)
    return _entry_dict(e)


@router.get("/production/entries/{entry_id}/materials")
def get_production_materials(entry_id: int, db: Session = Depends(get_db)):
    e = db.query(ProductionEntry).filter(ProductionEntry.id == entry_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Production entry not found")
    from backend.app.models import ProductionConsumedMaterial
    materials = db.query(ProductionConsumedMaterial).filter(ProductionConsumedMaterial.production_entry_id == entry_id).all()
    return [
        {
            "id": m.id,
            "raw_material_id": m.raw_material_id,
            "raw_material_name": m.raw_material.name if m.raw_material else None,
            "quantity_kg": float(m.quantity_kg or 0),
        }
        for m in materials
    ]


@router.delete("/production/entries/{entry_id}")
def archive_production_entry(entry_id: int, request: Request, db: Session = Depends(get_db)):
    e = db.query(ProductionEntry).filter(ProductionEntry.id == entry_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Production entry not found")
    reason = _change_reason(request)
    if not _archive_record(db, request, e, "production", "production_entry"):
        raise HTTPException(status_code=409, detail="Production entry already archived")
    _audit(db, request, "production", "archive", record=e, record_type="production_entry", old_values={"is_archived": False}, new_values={"is_archived": True}, notes=reason)
    db.commit()
    return {"status": "archived", "id": entry_id}


@router.post("/production/entries/{entry_id}/restore")
def restore_production_entry(entry_id: int, request: Request, db: Session = Depends(get_db)):
    e = db.query(ProductionEntry).filter(ProductionEntry.id == entry_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Production entry not found")
    reason = _change_reason(request)
    if not _restore_record(db, request, e, "production", "production_entry"):
        raise HTTPException(status_code=409, detail="Production entry is not archived")
    _audit(db, request, "production", "restore", record=e, record_type="production_entry", old_values={"is_archived": True}, new_values={"is_archived": False}, notes=reason)
    db.commit()
    return {"status": "restored", "id": entry_id}