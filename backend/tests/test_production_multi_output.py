"""Multi-size output for one production batch.

One batch is processed in bulk and divided into pack sizes only at
packaging, under a single batch number. Each size posts its own
finished-stock movement; raw consumption and stages stay bulk-level.

Run: python -m pytest backend/tests/test_production_multi_output.py
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
    return SimpleNamespace(headers={}, client=None)


@pytest.fixture
def setup(db):
    mat = models.RawMaterial(
        material_id="RM-T1", name="Test Raw", unit="kg",
        current_stock=Decimal("1000"), purchase_rate=Decimal("100"),
    )
    db.add(mat)
    prod = models.Product(product_id="P-T1", name="Test Masala")
    db.add(prod)
    db.flush()
    s250 = models.PackagingSize(
        product_id=prod.id, size_name="250g", weight_in_kg=Decimal("0.25"),
        rate_per_kg=Decimal("200"), packets_per_unit=1, is_active=True,
    )
    s50 = models.PackagingSize(
        product_id=prod.id, size_name="50g", weight_in_kg=Decimal("0.05"),
        rate_per_kg=Decimal("210"), packets_per_unit=1, is_active=True,
    )
    db.add_all([s250, s50])
    other = models.Product(product_id="P-T2", name="Other Masala")
    db.add(other)
    db.flush()
    foreign = models.PackagingSize(
        product_id=other.id, size_name="100g", weight_in_kg=Decimal("0.1"),
        rate_per_kg=Decimal("200"), packets_per_unit=1, is_active=True,
    )
    db.add(foreign)
    retired = models.PackagingSize(
        product_id=prod.id, size_name="1kg", weight_in_kg=Decimal("1"),
        rate_per_kg=Decimal("190"), packets_per_unit=1, is_active=False,
    )
    db.add(retired)
    db.commit()
    return {
        "mat": mat, "prod": prod, "s250": s250, "s50": s50,
        "foreign": foreign, "retired": retired,
    }


def _base(setup, outputs=None, **kw):
    payload = {
        "product_id": setup["prod"].id,
        "production_date": "2026-09-15T10:00:00",
        "target_quantity": 100,
        "consumed_materials": [
            {"raw_material_id": setup["mat"].id, "quantity": 100}
        ],
        "stages": [
            {"stage": "roasting", "input_quantity": 100, "output_quantity": 97},
            {"stage": "packaging", "input_quantity": 97, "output_quantity": 95},
        ],
    }
    if outputs is not None:
        payload["outputs"] = outputs
    else:
        payload["packaging_size_id"] = kw.pop("packaging_size_id", setup["s250"].id)
        payload["actual_output_packets"] = kw.pop("actual_output_packets", 200)
    payload.update(kw)
    return payload


def test_one_batch_posts_one_movement_per_size(db, setup):
    out = m.create_production_entry(_base(setup, outputs=[
        {"packaging_size_id": setup["s250"].id, "packets": 200},
        {"packaging_size_id": setup["s50"].id, "packets": 400},
    ]), _req(), db)

    assert Decimal(str(out["actual_output"])) == Decimal("70")  # 200*0.25 + 400*0.05
    assert len(out["outputs"]) == 2
    by_size = {o["packaging_size_id"]: o for o in out["outputs"]}
    assert by_size[setup["s250"].id]["quantity_kg"] == 50.0
    assert by_size[setup["s50"].id]["quantity_kg"] == 20.0

    moves = db.query(models.FinishedProductStockMovement).filter(
        models.FinishedProductStockMovement.reference_type == "production_entry",
        models.FinishedProductStockMovement.reference_id == out["id"],
    ).all()
    assert len(moves) == 2
    assert sorted(float(x.quantity) for x in moves) == [20.0, 50.0]


def test_legacy_single_size_payload_still_works(db, setup):
    out = m.create_production_entry(_base(setup), _req(), db)
    assert Decimal(str(out["actual_output"])) == Decimal("50")
    assert len(out["outputs"]) == 1
    assert out["outputs"][0]["packaging_size_id"] == setup["s250"].id


def test_duplicate_size_is_rejected(db, setup):
    with pytest.raises(HTTPException) as e:
        m.create_production_entry(_base(setup, outputs=[
            {"packaging_size_id": setup["s250"].id, "packets": 10},
            {"packaging_size_id": setup["s250"].id, "packets": 20},
        ]), _req(), db)
    assert e.value.status_code == 400


def test_retired_size_is_rejected(db, setup):
    with pytest.raises(HTTPException) as e:
        m.create_production_entry(_base(setup, outputs=[
            {"packaging_size_id": setup["retired"].id, "packets": 10},
        ]), _req(), db)
    assert e.value.status_code == 400


def test_other_products_size_is_rejected(db, setup):
    with pytest.raises(HTTPException) as e:
        m.create_production_entry(_base(setup, outputs=[
            {"packaging_size_id": setup["foreign"].id, "packets": 10},
        ]), _req(), db)
    assert e.value.status_code == 400


def test_zero_packets_is_rejected(db, setup):
    with pytest.raises(HTTPException) as e:
        m.create_production_entry(_base(setup, outputs=[
            {"packaging_size_id": setup["s250"].id, "packets": 0},
        ]), _req(), db)
    assert e.value.status_code == 400


def test_legacy_rows_serialize_from_movements(db, setup):
    out = m.create_production_entry(_base(setup), _req(), db)
    # Simulate a pre-multi-size row: no output rows, movements intact.
    db.query(models.ProductionOutput).delete()
    db.commit()
    entry = db.query(models.ProductionEntry).filter(
        models.ProductionEntry.id == out["id"]).first()
    d = m._production_to_dict(entry)
    assert len(d["outputs"]) == 1
    assert d["outputs"][0]["quantity_kg"] == 50.0
    assert d["outputs"][0]["packaging_size_name"] == "250g"
