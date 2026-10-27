"""Wrapper (pouch) weight tracking on production.

Wrapper rolls are ordinary raw materials stocked in kg; each pack size
carries its empty-pouch weight. A batch consumes pouches like any other
material, so packaging output can no longer exceed recorded input.

Run: python -m pytest backend/tests/test_wrapper_weight.py
"""

import os
import sys
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _req():
    return SimpleNamespace(query_params={"reason": "test change note"}, headers={}, client=None)


@pytest.fixture
def setup(db):
    spice = models.RawMaterial(
        material_id="RM-W1", name="Test Spice", unit="kg",
        current_stock=Decimal("1000"), purchase_rate=Decimal("100"),
    )
    roll = models.RawMaterial(
        material_id="RM-W2", name="Laminate Roll", unit="kg",
        current_stock=Decimal("500"), purchase_rate=Decimal("120"),
    )
    db.add_all([spice, roll])
    prod = models.Product(product_id="P-W1", name="Wrapped Masala")
    db.add(prod)
    db.flush()
    size = models.PackagingSize(
        product_id=prod.id, size_name="250g", weight_in_kg=Decimal("0.25"),
        rate_per_kg=Decimal("200"), packets_per_unit=1, is_active=True,
        wrapper_weight_kg=Decimal("0.008"),
    )
    db.add(size)
    db.commit()
    return {"spice": spice, "roll": roll, "prod": prod, "size": size}


def test_wrapper_consumption_posts_movement_and_deducts_stock(db, setup):
    out = m.create_production_entry({
        "product_id": setup["prod"].id,
        "production_date": "2026-09-15T10:00:00",
        "target_quantity": 50,
        "outputs": [{"packaging_size_id": setup["size"].id, "packets": 200}],
        "consumed_materials": [
            {"raw_material_id": setup["spice"].id, "quantity": 50},
            {"raw_material_id": setup["roll"].id, "quantity": 1.6},
        ],
        "stages": [{"stage": "mixing", "input_quantity": 51.6, "output_quantity": 50}],
    }, _req(), db)
    assert Decimal(str(out["actual_output"])) == Decimal("50")
    db.refresh(setup["roll"])
    assert Decimal(str(setup["roll"].current_stock)) == Decimal("498.4")
    moves = db.query(models.RawMaterialStockMovement).filter(
        models.RawMaterialStockMovement.reference_type == "production_entry",
        models.RawMaterialStockMovement.reference_id == out["id"],
    ).all()
    assert sorted(float(x.quantity) for x in moves) == [1.6, 50.0]


def test_wrapper_weight_saved_via_api_and_negative_refused(db, setup):
    out = m.update_packaging_size(
        setup["size"].id, {"wrapper_weight_kg": 0.01}, _req(), db)
    assert Decimal(str(out.wrapper_weight_kg)) == Decimal("0.01")
    with pytest.raises(HTTPException) as e:
        m.update_packaging_size(setup["size"].id, {"wrapper_weight_kg": -1}, _req(), db)
    assert e.value.status_code == 400


def test_zero_wrapper_weight_is_backward_compatible(db, setup):
    setup["size"].wrapper_weight_kg = Decimal("0")
    db.commit()
    out = m.create_production_entry({
        "product_id": setup["prod"].id,
        "production_date": "2026-09-15T10:00:00",
        "target_quantity": 50,
        "outputs": [{"packaging_size_id": setup["size"].id, "packets": 200}],
        "consumed_materials": [
            {"raw_material_id": setup["spice"].id, "quantity": 50}
        ],
        "stages": [{"stage": "mixing", "input_quantity": 50, "output_quantity": 50}],
    }, _req(), db)
    assert Decimal(str(out["actual_output"])) == Decimal("50")
