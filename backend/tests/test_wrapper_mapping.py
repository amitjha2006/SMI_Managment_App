"""Wrapper roll mapped per product + size, and remembered from production.

Each pack size may point at the raw material (printed film roll) its pouches
are made from. Production uses that mapping to derive which rolls a run
consumes; a roll picked for an unmapped size on the form is stored with the
entry so the next run fills itself.

Run: python -m pytest backend/tests/test_wrapper_mapping.py -v
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
from backend.app.schemas import PackagingSizeSchema  # noqa: E402


def _req():
    return SimpleNamespace(query_params={"reason": "test change note"}, headers={}, client=None)


@pytest.fixture
def setup(db):
    spice = models.RawMaterial(
        material_id="RM-M1", name="Mapping Spice", unit="kg",
        current_stock=Decimal("1000"), purchase_rate=Decimal("100"),
    )
    roll_50 = models.RawMaterial(
        material_id="RM-M2", name="Turmeric 50g Roll", unit="kg",
        current_stock=Decimal("100"), purchase_rate=Decimal("300"),
    )
    roll_250 = models.RawMaterial(
        material_id="RM-M3", name="Turmeric 250g Roll", unit="kg",
        current_stock=Decimal("100"), purchase_rate=Decimal("300"),
    )
    dead = models.RawMaterial(
        material_id="RM-M4", name="Old Roll", unit="kg",
        current_stock=Decimal("0"), purchase_rate=Decimal("1"), is_active=False,
    )
    db.add_all([spice, roll_50, roll_250, dead])
    prod = models.Product(product_id="P-M1", name="Turmeric Powder")
    db.add(prod)
    db.flush()
    s50 = models.PackagingSize(
        product_id=prod.id, size_name="50g", weight_in_kg=Decimal("0.05"),
        rate_per_kg=Decimal("200"), packets_per_unit=1, is_active=True,
        wrapper_weight_kg=Decimal("0.004"), wrapper_material_id=roll_50.id,
    )
    s250 = models.PackagingSize(
        product_id=prod.id, size_name="250g", weight_in_kg=Decimal("0.25"),
        rate_per_kg=Decimal("200"), packets_per_unit=1, is_active=True,
        wrapper_weight_kg=Decimal("0.008"), wrapper_material_id=roll_250.id,
    )
    db.add_all([s50, s250])
    db.commit()
    return {
        "spice": spice, "roll_50": roll_50, "roll_250": roll_250, "dead": dead,
        "prod": prod, "s50": s50, "s250": s250,
    }


def test_schema_exposes_the_mapping():
    assert "wrapper_material_id" in PackagingSizeSchema.model_fields


def test_mapping_saved_when_a_product_is_created(db, setup):
    out = m.create_product({
        "name": "New Roll Masala",
        "packaging_sizes": [{
            "size_name": "50g", "weight_in_kg": 0.05, "rate_per_kg": 100,
            "wrapper_material_id": setup["roll_50"].id,
        }],
    }, _req(), db)
    size = (
        db.query(models.PackagingSize)
        .filter(models.PackagingSize.product_id == out.id)
        .one()
    )
    assert size.wrapper_material_id == setup["roll_50"].id


def test_mapping_saved_and_cleared_on_the_size_editor(db, setup):
    m.update_packaging_size(
        setup["s250"].id, {"wrapper_material_id": setup["roll_50"].id}, _req(), db
    )
    db.refresh(setup["s250"])
    assert setup["s250"].wrapper_material_id == setup["roll_50"].id

    m.update_packaging_size(setup["s250"].id, {"wrapper_material_id": None}, _req(), db)
    db.refresh(setup["s250"])
    assert setup["s250"].wrapper_material_id is None


def test_unknown_or_inactive_roll_is_refused(db, setup):
    with pytest.raises(HTTPException) as e:
        m.update_packaging_size(setup["s250"].id, {"wrapper_material_id": 999999}, _req(), db)
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        m.update_packaging_size(
            setup["s250"].id, {"wrapper_material_id": setup["dead"].id}, _req(), db
        )
    assert e.value.status_code == 400


def _produce(db, setup, outputs, consumed, mappings=None):
    payload = {
        "product_id": setup["prod"].id,
        "production_date": "2026-10-01T09:00:00",
        "target_quantity": 100,
        "outputs": outputs,
        "consumed_materials": consumed,
        "stages": [{"stage": "packaging", "input_quantity": 99, "output_quantity": 100}],
    }
    if mappings is not None:
        payload["wrapper_mappings"] = mappings
    return m.create_production_entry(payload, _req(), db)


def test_two_sizes_consume_their_own_roles(db, setup):
    """50g x 100 packets = 0.4 kg of the 50g roll; 250g x 40 = 0.32 kg."""
    out = _produce(
        db, setup,
        outputs=[
            {"packaging_size_id": setup["s50"].id, "packets": 100},
            {"packaging_size_id": setup["s250"].id, "packets": 40},
        ],
        consumed=[
            {"raw_material_id": setup["spice"].id, "quantity": 5},
            {"raw_material_id": setup["roll_50"].id, "quantity": 0.4},
            {"raw_material_id": setup["roll_250"].id, "quantity": 0.32},
        ],
    )
    db.refresh(setup["roll_50"])
    db.refresh(setup["roll_250"])
    assert Decimal(str(setup["roll_50"].current_stock)) == Decimal("99.6")
    assert Decimal(str(setup["roll_250"].current_stock)) == Decimal("99.68")
    moves = db.query(models.RawMaterialStockMovement).filter(
        models.RawMaterialStockMovement.reference_type == "production_entry",
        models.RawMaterialStockMovement.reference_id == out["id"],
    ).all()
    assert sorted(round(float(x.quantity), 3) for x in moves) == [0.32, 0.4, 5.0]


def test_roll_picked_at_production_is_remembered(db, setup):
    setup["s50"].wrapper_material_id = None
    db.commit()
    _produce(
        db, setup,
        outputs=[{"packaging_size_id": setup["s50"].id, "packets": 10}],
        consumed=[
            {"raw_material_id": setup["spice"].id, "quantity": 1},
            {"raw_material_id": setup["roll_50"].id, "quantity": 0.04},
        ],
        mappings=[{"packaging_size_id": setup["s50"].id,
                   "raw_material_id": setup["roll_50"].id}],
    )
    db.refresh(setup["s50"])
    assert setup["s50"].wrapper_material_id == setup["roll_50"].id


def test_mapping_for_another_products_size_is_refused(db, setup):
    other = m.create_product({
        "name": "Other Masala",
        "packaging_sizes": [{"size_name": "50g", "weight_in_kg": 0.05, "rate_per_kg": 100}],
    }, _req(), db)
    other_size = (
        db.query(models.PackagingSize)
        .filter(models.PackagingSize.product_id == other.id)
        .one()
    )
    with pytest.raises(HTTPException) as e:
        _produce(
            db, setup,
            outputs=[{"packaging_size_id": setup["s50"].id, "packets": 10}],
            consumed=[{"raw_material_id": setup["spice"].id, "quantity": 1}],
            mappings=[{"packaging_size_id": other_size.id,
                       "raw_material_id": setup["roll_50"].id}],
        )
    assert e.value.status_code == 400

def test_seeder_does_not_confuse_250g_with_the_50g_size():
    """The seeder matched substrings, so "Turmeric 250g" looked like both the
    250g and the 50g size and was skipped as ambiguous."""
    import importlib.util
    import os

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    spec = importlib.util.spec_from_file_location(
        "seed_wrapper_mappings", os.path.join(root, "scripts", "seed_wrapper_mappings.py")
    )
    seeder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seeder)

    assert seeder.size_in("pauch packet label turmeric 250g", "250g")
    assert not seeder.size_in("pauch packet label turmeric 250g", "50g")
    assert seeder.size_in("pauch packet label cumin 50g", "50g")
    assert seeder.size_in("turmeric powder loose powder", "loose powder")
    assert not seeder.size_in("turmeric powder 500g", "50g")