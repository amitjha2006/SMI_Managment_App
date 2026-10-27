"""Product, category, packaging, formulation, workflow routes"""
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import (
    HSNCode,
    PackagingSize,
    Product,
    ProductCategory,
    ProductFormulation,
    ProductFormulationStep,
    PrintFooter,
    RawMaterial,
    SizeRateHistory,
)
from backend.app.schemas import (
    PackagingSizeSchema,
    ProductCategoryCreate,
    ProductCategorySchema,
    ProductCategoryUpdate,
    ProductSchema,
    ProductUpdate,
    SizeRateHistorySchema,
)
from backend.app.core.shared import (
    _audit,
    _change_reason,
    _reject_if_archived,
    _archive_record,
    _restore_record,
    _ist_wall,
    _request_user_and_role,
    _FULL_PERCENT_SHARE,
)

router = APIRouter()


@router.get("/products", response_model=list[ProductSchema])
def list_products(db: Session = Depends(get_db)):
    return db.query(Product).filter(Product.is_active == True).order_by(Product.name).all()  # noqa: E712


@router.post("/products", response_model=ProductSchema)
def create_product(product: ProductSchema, request: Request, db: Session = Depends(get_db)):
    # Allow direct dict calls (test harness) as well as FastAPI-parsed schemas.
    if isinstance(product, dict):
        product = ProductSchema.model_construct(
            id=0, product_id="", is_active=True, created_at=None,
            **{k: v for k, v in product.items() if k in ProductSchema.model_fields}
        )
    # ProductSchema is used for both create and response; validate required fields
    if not product.name:
        raise HTTPException(status_code=400, detail="name required")
    if db.query(Product).filter(Product.name == product.name).first():
        raise HTTPException(status_code=400, detail="Product with this name already exists")
    p = Product(
        name=product.name,
        product_id=product.product_id or "".join(c for c in product.name if c.isalnum())[:20].upper() or "PROD",
        category_id=product.category_id,
        hsn_code=product.hsn_code,
        gst_rate=product.gst_rate or 0,
        base_rate_per_kg=product.base_rate_per_kg or 0,
    )
    db.add(p)
    db.flush()

    # Create packaging sizes from the payload, normalising loose spellings
    # and deduplicating by size name (case-insensitive).
    seen_names = set()
    for size_data in (product.packaging_sizes or []):
        if isinstance(size_data, dict):
            g = lambda k, d=None: size_data.get(k, d)
        else:
            g = lambda k, d=None: getattr(size_data, k, d)
        size_name = g("size_name")
        if not size_name:
            continue
        if size_name.strip().casefold() in ("loose", "loose masala"):
            size_name = "Loose Powder"
        if size_name.strip().casefold() in seen_names:
            continue
        seen_names.add(size_name.strip().casefold())
        size = PackagingSize(
            product_id=p.id,
            size_name=size_name,
            weight_in_kg=g("weight_in_kg"),
            rate_per_kg=g("rate_per_kg") or 0,
            packets_per_unit=g("packets_per_unit") or 1,
            threshold_packets=g("threshold_packets") or 0,
            wrapper_weight_kg=g("wrapper_weight_kg") or 0,
            wrapper_material_id=g("wrapper_material_id"),
            is_active=True,
        )
        db.add(size)
    _audit(db, request, "products", "create", record=p, record_type="product", new_values={"name": p.name})
    db.commit()
    db.refresh(p)
    return p


@router.get("/products/{product_id}", response_model=ProductSchema)
def get_product(product_id: int, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    return p


@router.put("/products/{product_id}", response_model=ProductSchema)
def update_product(product_id: int, product: ProductUpdate, request: Request, db: Session = Depends(get_db)):
    # Allow direct dict calls (test harness) as well as FastAPI-parsed schemas.
    if isinstance(product, dict):
        product = ProductUpdate.model_construct(**product)
    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    _reject_if_archived(p, "product")
    reason = _change_reason(request)
    old = {"name": p.name, "category_id": p.category_id, "hsn_code": p.hsn_code, "gst_rate": p.gst_rate, "base_rate_per_kg": p.base_rate_per_kg}
    data = product.dict(exclude_unset=True)
    # Handle category: frontend may send category_id (existing) or category (new name)
    # The 'category' property is read-only, so we must not try to set it.
    category_val = data.pop("category", None)
    if isinstance(category_val, str) and category_val.strip():
        # New category name provided - look it up
        cat = db.query(ProductCategory).filter(ProductCategory.name == category_val.strip()).first()
        if cat:
            data["category_id"] = cat.id
        else:
            raise HTTPException(status_code=400, detail=f"Category '{category_val}' not found")
    # If category_val is None/null, ignore it - category_id will be used if provided
    
    for field, value in data.items():
        if value is not None:
            setattr(p, field, value)
    _audit(db, request, "products", "update", record=p, record_type="product", old_values=old, new_values={"name": p.name, "category_id": p.category_id, "hsn_code": p.hsn_code, "gst_rate": p.gst_rate, "base_rate_per_kg": p.base_rate_per_kg}, notes=reason)
    db.commit()
    db.refresh(p)
    return p


@router.delete("/products/{product_id}")
def delete_product(product_id: int, request: Request, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    reason = _change_reason(request)
    if not _archive_record(db, request, p, "products", "product"):
        raise HTTPException(status_code=409, detail="Product already archived")
    _audit(db, request, "products", "archive", record=p, record_type="product", old_values={"is_active": True, "is_archived": False}, new_values={"is_active": False, "is_archived": True}, notes=reason)
    db.commit()
    return {"status": "archived", "id": product_id}


@router.post("/products/{product_id}/restore")
def restore_product(product_id: int, request: Request, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    reason = _change_reason(request)
    if not _restore_record(db, request, p, "products", "product"):
        raise HTTPException(status_code=409, detail="Product is not archived")
    _audit(db, request, "products", "restore", record=p, record_type="product", old_values={"is_archived": True}, new_values={"is_archived": False}, notes=reason)
    db.commit()
    return {"status": "restored", "id": product_id}


# Product categories
@router.get("/product-categories", response_model=list[ProductCategorySchema])
def list_product_categories(db: Session = Depends(get_db)):
    return db.query(ProductCategory).order_by(ProductCategory.name).all()


@router.post("/product-categories", response_model=ProductCategorySchema)
def create_product_category(category: ProductCategoryCreate, request: Request, db: Session = Depends(get_db)):
    # Allow direct dict calls (test harness) as well as FastAPI-parsed schemas.
    if isinstance(category, dict):
        category = ProductCategoryCreate(**category)
    if not category.name:
        raise HTTPException(status_code=400, detail="name required")
    if db.query(ProductCategory).filter(ProductCategory.name == category.name).first():
        raise HTTPException(status_code=400, detail="Category already exists")
    cat = ProductCategory(name=category.name, description=category.description, parent_id=category.parent_id)
    db.add(cat)
    _audit(db, request, "products", "category_create", record=cat, record_type="product_category", new_values={"name": cat.name})
    db.commit()
    db.refresh(cat)
    return cat


@router.put("/product-categories/{category_id}", response_model=ProductCategorySchema)
def update_product_category(category_id: int, category: ProductCategoryUpdate, request: Request, db: Session = Depends(get_db)):
    # Allow direct dict calls (test harness) as well as FastAPI-parsed schemas.
    if isinstance(category, dict):
        category = ProductCategoryUpdate(**category)
    cat = db.query(ProductCategory).filter(ProductCategory.id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    if isinstance(category, dict):
        category = ProductCategoryCreate.model_construct(**category)
    if isinstance(category, dict):
        category = ProductCategoryUpdate.model_construct(**category)
    data = category.dict(exclude_unset=True)
    old = {"name": cat.name, "description": cat.description, "is_active": cat.is_active, "parent_id": cat.parent_id}
    for field, value in data.items():
        if value is not None:
            setattr(cat, field, value)
    _audit(db, request, "products", "category_update", record=cat, record_type="product_category", old_values=old, new_values={"name": cat.name, "description": cat.description, "is_active": cat.is_active, "parent_id": cat.parent_id})
    db.commit()
    return cat


@router.delete("/product-categories/{category_id}")
def delete_product_category(category_id: int, request: Request, db: Session = Depends(get_db)):
    cat = db.query(ProductCategory).filter(ProductCategory.id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    # Check if any products use this category
    if db.query(Product).filter(Product.category_id == category_id).first():
        raise HTTPException(status_code=409, detail="Cannot delete: products use this category")
    _audit(db, request, "products", "category_delete", record=cat, record_type="product_category", old_values={"name": cat.name})
    db.delete(cat)
    db.commit()
    return {"status": "deleted", "id": category_id}


# HSN codes
@router.get("/hsn-codes")
def list_hsn_codes(db: Session = Depends(get_db)):
    return db.query(HSNCode).order_by(HSNCode.code).all()


@router.get("/hsn-codes/suggest")
def suggest_hsn_codes(q: str = "", db: Session = Depends(get_db)):
    if not q or len(q) < 2:
        return []
    codes = db.query(HSNCode).filter(HSNCode.code.ilike(f"%{q}%") | HSNCode.description.ilike(f"%{q}%")).limit(10).all()
    return [{"code": c.code, "description": c.description, "gst_rate": float(c.gst_rate or 0)} for c in codes]


# Print footers
@router.get("/print-footers")
def list_print_footers(db: Session = Depends(get_db)):
    return db.query(PrintFooter).order_by(PrintFooter.id.desc()).all()


@router.post("/print-footers")
def create_print_footer(payload: dict, request: Request, db: Session = Depends(get_db)):
    text = (payload or {}).get("text")
    if not text:
        raise HTTPException(status_code=400, detail="text required")
    footer = PrintFooter(text=text, is_default=(payload or {}).get("is_default", False))
    if footer.is_default:
        db.query(PrintFooter).filter(PrintFooter.is_default == True).update({PrintFooter.is_default: False})  # noqa: E712
    db.add(footer)
    _audit(db, request, "settings", "footer_create", record=footer, record_type="print_footer", new_values={"text": text})
    db.commit()
    db.refresh(footer)
    return footer


@router.put("/print-footers/{footer_id}")
def update_print_footer(footer_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    footer = db.query(PrintFooter).filter(PrintFooter.id == footer_id).first()
    if not footer:
        raise HTTPException(status_code=404, detail="Footer not found")
    text = (payload or {}).get("text")
    is_default = (payload or {}).get("is_default")
    if text is not None:
        footer.text = text
    if is_default is not None:
        if is_default:
            db.query(PrintFooter).filter(PrintFooter.is_default == True).update({PrintFooter.is_default: False})  # noqa: E712
        footer.is_default = is_default
    _audit(db, request, "settings", "footer_update", record=footer, record_type="print_footer", new_values={"text": footer.text, "is_default": footer.is_default})
    db.commit()
    return footer


@router.delete("/print-footers/{footer_id}")
def delete_print_footer(footer_id: int, request: Request, db: Session = Depends(get_db)):
    footer = db.query(PrintFooter).filter(PrintFooter.id == footer_id).first()
    if not footer:
        raise HTTPException(status_code=404, detail="Footer not found")
    _audit(db, request, "settings", "footer_delete", record=footer, record_type="print_footer", old_values={"text": footer.text})
    db.delete(footer)
    db.commit()
    return {"status": "deleted", "id": footer_id}


# Packaging sizes
# Every field the size editor sends. Keep in sync with PackagingSizeBase.
_SIZE_FIELDS = (
    "size_name",
    "weight_in_kg",
    "rate_per_kg",
    "packets_per_unit",
    "threshold_packets",
    "wrapper_weight_kg",
    "wrapper_material_id",
)


def _size_snapshot(size: PackagingSize) -> dict:
    """JSON-safe pack size view for audit diffs."""
    snap = {}
    for field in _SIZE_FIELDS:
        value = getattr(size, field)
        snap[field] = float(value) if isinstance(value, Decimal) else value
    return snap


@router.put("/packaging-sizes/{size_id}", response_model=PackagingSizeSchema)
def update_packaging_size(size_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    size = db.query(PackagingSize).filter(PackagingSize.id == size_id).first()
    if not size:
        raise HTTPException(status_code=404, detail="Packaging size not found")
    _reject_if_archived(size, "packaging size")
    reason = _change_reason(request)
    old = _size_snapshot(size)

    def num(value):
        return Decimal(str(0 if value is None else value))

    if "weight_in_kg" in payload and num(payload["weight_in_kg"]) <= 0:
        raise HTTPException(status_code=400, detail="weight_in_kg must be greater than 0")
    for field, message in (
        ("rate_per_kg", "rate_per_kg cannot be negative"),
        ("threshold_packets", "threshold_packets cannot be negative"),
        ("wrapper_weight_kg", "wrapper_weight_kg cannot be negative"),
    ):
        if field in payload and num(payload[field]) < 0:
            raise HTTPException(status_code=400, detail=message)
    if payload.get("wrapper_material_id") is not None:
        roll = db.query(RawMaterial).filter(RawMaterial.id == payload["wrapper_material_id"]).first()
        if not roll or not roll.is_active:
            raise HTTPException(status_code=400, detail="Wrapper roll not found or inactive")

    rate_before = size.rate_per_kg
    for field in _SIZE_FIELDS:
        if field in payload:
            setattr(size, field, payload[field])
    if "rate" in payload and "rate_per_kg" not in payload:
        size.rate_per_kg = payload["rate"]  # legacy key from older clients
    if size.rate_per_kg != rate_before:
        user, _role = _request_user_and_role(db, request)
        db.add(SizeRateHistory(
            packaging_size_id=size.id,
            old_rate=rate_before or 0,
            new_rate=size.rate_per_kg or 0,
            changed_by=user.id if user else None,
        ))
    _audit(db, request, "products", "packaging_update", record=size, record_type="packaging_size", old_values=old, new_values=_size_snapshot(size), notes=reason)
    db.commit()
    db.refresh(size)
    return size


@router.get("/packaging-sizes/{size_id}/rate-history", response_model=list[SizeRateHistorySchema])
def size_rate_history(size_id: int, db: Session = Depends(get_db)):
    rows = (
        db.query(SizeRateHistory)
        .filter(SizeRateHistory.packaging_size_id == size_id)
        .order_by(SizeRateHistory.changed_at.desc(), SizeRateHistory.id.desc())
        .all()
    )
    return [SizeRateHistorySchema.model_validate(row).model_dump() for row in rows]


@router.delete("/packaging-sizes/{size_id}")
def delete_packaging_size(size_id: int, request: Request, db: Session = Depends(get_db)):
    size = db.query(PackagingSize).filter(PackagingSize.id == size_id).first()
    if not size:
        raise HTTPException(status_code=404, detail="Packaging size not found")
    # Check if any stock movements reference this size
    if db.query(FinishedProductStockMovement).filter(FinishedProductStockMovement.packaging_size_id == size_id).first():
        raise HTTPException(status_code=409, detail="Cannot delete: stock movements reference this size")
    _audit(db, request, "products", "packaging_delete", record=size, record_type="packaging_size", old_values={"size_name": size.size_name})
    db.delete(size)
    db.commit()
    return {"status": "deleted", "id": size_id}


# Product workflow
@router.get("/products/{product_id}/workflow")
def get_product_workflow(product_id: int, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    steps = db.query(ProductFormulationStep).filter(ProductFormulationStep.product_id == product_id).order_by(ProductFormulationStep.step_order).all()
    return {
        "product_id": product_id,
        "product_name": p.name,
        "steps": [
            {
                "id": s.id,
                "step_order": s.step_order,
                "name": s.name,
                "output_weight_kg": float(s.output_weight_kg or 0),
                "expected_yield_percent": float(s.expected_yield_percent or 0),
            }
            for s in steps
        ],
    }


@router.put("/products/{product_id}/workflow")
def update_product_workflow(product_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    _reject_if_archived(p, "product")
    steps_data = (payload or {}).get("steps") or []
    # Delete existing steps
    db.query(ProductFormulationStep).filter(ProductFormulationStep.product_id == product_id).delete()
    # Add new steps
    for idx, step in enumerate(steps_data):
        s = ProductFormulationStep(
            product_id=product_id,
            step_order=step.get("step_order", idx + 1),
            name=step.get("name", f"Step {idx + 1}"),
            output_weight_kg=step.get("output_weight_kg"),
            expected_yield_percent=step.get("expected_yield_percent"),
        )
        db.add(s)
    _audit(db, request, "products", "workflow_update", record=p, record_type="product", new_values={"step_count": len(steps_data)})
    db.commit()
    return {"status": "saved", "product_id": product_id, "step_count": len(steps_data)}


# Product formulation
@router.get("/products/{product_id}/formulation")
def get_product_formulation(product_id: int, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    formulation = db.query(ProductFormulation).filter(ProductFormulation.product_id == product_id).all()
    items = []
    estimated_cost_per_kg = Decimal("0")
    for f in formulation:
        mat = f.raw_material
        rate = float(mat.purchase_rate or 0) if mat else 0.0
        pct = float(f.percentage or 0)
        estimated_cost_per_kg += Decimal(str(rate)) * Decimal(str(pct)) / Decimal("100")
        items.append({
            "id": f.id,
            "raw_material_id": f.raw_material_id,
            "material_name": mat.name if mat else None,
            "percentage": float(f.percentage or 0),
        })
    return {
        "product_id": product_id,
        "product_name": p.name,
        "items": items,
        "estimated_cost_per_kg": float(estimated_cost_per_kg),
        "help_text": p.help_text,
        "steps": [
            {
                "id": s.id,
                "stage": s.stage,
                "instruction": s.instruction,
                "step_order": s.sequence,
            }
            for s in db.query(ProductFormulationStep)
            .filter(ProductFormulationStep.product_id == product_id)
            .order_by(ProductFormulationStep.sequence).all()
        ],
    }


_VALID_STAGES = {"roasting", "grinding", "mixing", "packaging"}


@router.put("/products/{product_id}/formulation")
def update_product_formulation(product_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    _reject_if_archived(p, "product")
    items = (payload or {}).get("items")
    if items is not None:
        if not isinstance(items, list):
            raise HTTPException(status_code=400, detail="items must be a list")
        if len(items) > 0:
            seen_ids = set()
            for item in items:
                mid = item.get("raw_material_id")
                if mid is None:
                    raise HTTPException(status_code=400, detail="raw_material_id required")
                if mid in seen_ids:
                    raise HTTPException(status_code=400, detail=f"Duplicate material '{mid}'")
                seen_ids.add(mid)
                if not db.query(RawMaterial).filter(RawMaterial.id == mid).first():
                    raise HTTPException(status_code=400, detail=f"Unknown material '{mid}'")
                try:
                    pct = float(item.get("percentage", 0))
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail=f"Invalid percentage '{item.get('percentage')}'")
                if pct < 0 or pct > 100:
                    raise HTTPException(status_code=400, detail=f"Percentage {pct} out of range (0-100)")
            total_pct = sum(float(i.get("percentage", 0)) for i in items)
            if abs(total_pct - _FULL_PERCENT_SHARE) > 0.01:
                raise HTTPException(status_code=400, detail=f"Formulation percentages must total {_FULL_PERCENT_SHARE}% (got {total_pct}%)")
        # Delete existing
        db.query(ProductFormulation).filter(ProductFormulation.product_id == product_id).delete()
        for item in items:
            f = ProductFormulation(
                product_id=product_id,
                raw_material_id=item.get("raw_material_id"),
                percentage=item.get("percentage"),
            )
            db.add(f)

    steps = (payload or {}).get("steps")
    if steps is not None:
        if not isinstance(steps, list):
            raise HTTPException(status_code=400, detail="steps must be a list")
        db.query(ProductFormulationStep).filter(ProductFormulationStep.product_id == product_id).delete()
        for idx, step in enumerate(steps):
            stage = step.get("stage")
            if stage is not None:
                stage = stage.strip().lower() or None
            if stage is not None and stage not in _VALID_STAGES:
                raise HTTPException(status_code=400, detail=f"Unknown stage '{stage}'")
            instruction = step.get("instruction", "").strip()
            if not instruction:
                raise HTTPException(status_code=400, detail="instruction required")
            s = ProductFormulationStep(
                product_id=product_id,
                sequence=step.get("step_order", idx + 1),
                stage=stage,
                instruction=instruction,
            )
            db.add(s)
    db.commit()
    # Re-query so the caller gets back the saved items with material names.
    saved = db.query(ProductFormulation).filter(ProductFormulation.product_id == product_id).all()
    out_items = []
    estimated_cost_per_kg = Decimal("0")
    for f in saved:
        mat = f.raw_material
        rate = float(mat.purchase_rate or 0) if mat else 0.0
        pct = float(f.percentage or 0)
        estimated_cost_per_kg += Decimal(str(rate)) * Decimal(str(pct)) / Decimal("100")
        out_items.append({
            "id": f.id,
            "raw_material_id": f.raw_material_id,
            "material_name": mat.name if mat else None,
            "percentage": float(f.percentage or 0),
        })
    step_rows = db.query(ProductFormulationStep).filter(ProductFormulationStep.product_id == product_id).order_by(ProductFormulationStep.sequence).all()
    out_steps = []
    for s in step_rows:
        out_steps.append({
            "id": s.id,
            "stage": s.stage,
            "instruction": s.instruction,
            "step_order": s.sequence,
        })
    return {"status": "saved", "product_id": product_id, "item_count": len(items) if items is not None else 0, "items": out_items, "estimated_cost_per_kg": float(estimated_cost_per_kg), "steps": out_steps}


# Product help tables
@router.get("/products/{product_id}/help")
def get_product_help(product_id: int, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    return {
        "product_id": product_id,
        "product_name": p.name,
        "help_text": p.help_text,
    }


@router.put("/products/{product_id}/help")
def update_product_help(product_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    _reject_if_archived(p, "product")
    help_text = (payload or {}).get("help_text")
    if help_text is not None:
        help_text = help_text.strip() or None
    p.help_text = help_text
    _audit(db, request, "products", "help_update", record=p, record_type="product", new_values={"help_text": help_text})
    db.commit()
    db.refresh(p)
    return {"status": "saved", "product_id": product_id, "help_text": p.help_text}


# Reports by category
@router.get("/reports/by-category")
def report_by_category(
    category_id: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    db: Session = Depends(get_db),
):
    """Sales report grouped by product category."""
    from datetime import datetime
    from backend.app.services import challan_snapshot as _snap

    from backend.app.models import SalesChallan
    q = db.query(SalesChallan).filter(SalesChallan.is_archived == False)  # noqa: E712
    if from_date:
        q = q.filter(SalesChallan.challan_date >= datetime.fromisoformat(from_date).date())
    if to_date:
        q = q.filter(SalesChallan.challan_date <= datetime.fromisoformat(to_date).date())
    challans = q.all()

    # Get balances
    balances = _snap.all_challan_balances(db)

    # Group by category via challan items
    cat_totals = {}
    for ch in challans:
        for item in ch.items:
            cat_name = item.product.category.name if item.product and item.product.category else "Uncategorized"
            if category_id and item.product and item.product.category_id != category_id:
                continue
            cat_totals[cat_name] = cat_totals.get(cat_name, {"qty": 0, "amount": 0})
            cat_totals[cat_name]["qty"] += float(item.quantity or 0)
            cat_totals[cat_name]["amount"] += float(item.amount or 0)

    return [{"category": k, "quantity": v["qty"], "amount": v["amount"]} for k, v in cat_totals.items()]