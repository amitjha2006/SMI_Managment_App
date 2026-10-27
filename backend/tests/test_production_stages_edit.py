"""Editing the stage rows of a saved production entry.

Stage rows are process records: correcting them must rewrite the entry's
wastage/gain figures without ever moving stock.

Run: python -m pytest backend/tests/test_production_stages_edit.py -v
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

import backend.main as m
from backend.app import models


def _req():
    return SimpleNamespace(headers={}, client=None)


@pytest.fixture
def setup(db):
    mat = models.RawMaterial(
        material_id="RM-SE1", name="Stage Edit Raw", unit="kg",
        current_stock=Decimal("10000"), purchase_rate=Decimal("100"),
    )
    db.add(mat)
    prod = models.Product(product_id="P-SE1", name="Stage Edit Masala")
    db.add(prod)
    db.flush()
    db.commit()
    return {"mat": mat, "prod": prod}


def _save(db, setup):
    return m.create_production_entry(
        {
            "product_id": setup["prod"].id,
            "production_date": "2026-10-22T10:00:00",
            "target_quantity": 50,
            "outputs": [],
            "consumed_materials": [{"raw_material_id": setup["mat"].id, "quantity": 50}],
            "stages": [
                {"stage": "grinding", "input_quantity": 50, "output_quantity": 48},
            ],
            "actual_output": 48,
        },
        _req(), db,
    )


def test_edit_stages_rewrites_wastage(db, setup):
    out = _save(db, setup)
    first = out["stage_entries"][0]
    assert first.output_quantity == Decimal("48")
    assert first.wastage_quantity == Decimal("2")

    m.update_production_stages(
        out["id"],
        {"stages": [{"stage": "grinding", "input_quantity": 50, "output_quantity": 45}]},
        _req(), db,
    )

    row = (
        db.query(models.ProductionStageEntry)
        .filter_by(production_entry_id=out["id"])
        .first()
    )
    assert row.output_quantity == Decimal("45")
    assert row.wastage_quantity == Decimal("5")
    assert row.wastage_percentage == Decimal("10")  # 5 of 50 kg


def test_edit_stages_rejects_unknown_stage_and_never_moves_stock(db, setup):
    out = _save(db, setup)
    mat = db.query(models.RawMaterial).filter_by(id=setup["mat"].id).first()
    before = mat.current_stock
    with pytest.raises(m.HTTPException) as exc:
        m.update_production_stages(
            out["id"],
            {"stages": [{"stage": "frying", "input_quantity": 1, "output_quantity": 1}]},
            _req(), db,
        )
    assert exc.value.status_code == 400
    # Stage edits are process records only: stock never moves.
    assert mat.current_stock == before


def test_edit_stages_include_packaging_gain(db, setup):
    """A corrected packaging row keeps its wrapper-gain treatment."""
    out = _save(db, setup)
    m.update_production_stages(
        out["id"],
        {"stages": [
            {"stage": "grinding", "input_quantity": 50, "output_quantity": 50},
            {"stage": "packaging", "input_quantity": 50, "output_quantity": 52},
        ]},
        _req(), db,
    )
    rows = (
        db.query(models.ProductionStageEntry)
        .filter_by(production_entry_id=out["id"])
        .order_by(models.ProductionStageEntry.sequence)
        .all()
    )
    assert rows[0].wastage_quantity == Decimal("0")
    # 52 > 50 on the packaging stage: surplus is packing gain, not wastage.
    assert rows[1].packing_gain_kg == Decimal("2")
    assert rows[1].wastage_quantity == Decimal("0")
