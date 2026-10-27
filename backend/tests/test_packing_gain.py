"""Packing gain on packaging stages (wrapper weight).

Packed output can legitimately exceed its loose input because the wrapper
adds real weight. That surplus is stored as packing_gain_kg on packaging
stages only - never as negative wastage, and never netted against true
loss in any total. Other stages keep the clamp-to-zero behaviour.

Run: python -m pytest backend/tests/test_packing_gain.py -v
"""

import os
import sys
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest  # noqa: E402

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402
from backend.app.services.production_wastage import stage_loss  # noqa: E402


def _req():
    return SimpleNamespace(headers={}, client=None)


@pytest.fixture
def setup(db):
    mat = models.RawMaterial(
        material_id="RM-G1", name="Test Gain Raw", unit="kg",
        current_stock=Decimal("10000"), purchase_rate=Decimal("100"),
    )
    db.add(mat)
    prod = models.Product(product_id="P-G1", name="Gain Masala")
    db.add(prod)
    db.flush()
    size = models.PackagingSize(
        product_id=prod.id, size_name="250g", weight_in_kg=Decimal("0.25"),
        rate_per_kg=Decimal("200"), packets_per_unit=1, is_active=True,
    )
    db.add_all([size])
    db.commit()
    return {"mat": mat, "prod": prod, "size": size}


def _produce(db, setup, stages):
    return m.create_production_entry({
        "product_id": setup["prod"].id,
        "production_date": "2026-09-15T10:00:00",
        "target_quantity": 50,
        "outputs": [{"packaging_size_id": setup["size"].id, "packets": 200}],
        "consumed_materials": [{"raw_material_id": setup["mat"].id, "quantity": 50}],
        "stages": stages,
    }, _req(), db)


def test_packaging_surplus_is_gain_not_loss():
    loss = stage_loss(50, 52.5, is_packaging=True)
    assert loss.packing_gain_kg == Decimal("2.500")
    assert loss.wastage_kg == Decimal("0.000")
    assert loss.wastage_pct == Decimal("0.00")


def test_other_stages_still_clamp_to_zero():
    for in_q, out_q in ((50, 52.5), (10, 12)):
        loss = stage_loss(in_q, out_q)
        assert loss.packing_gain_kg == Decimal("0.000")
        assert loss.wastage_kg == Decimal("0.000")


def test_gain_defaults_off_for_existing_callers():
    loss = stage_loss(10, 12)
    assert loss.packing_gain_kg == Decimal("0.000")


def test_save_path_stores_gain_on_packaging(db, setup):
    out = _produce(db, setup, [
        {"stage": "mixing", "input_quantity": 50, "output_quantity": 49},
        {"stage": "packaging", "input_quantity": 49, "output_quantity": 50.5},
    ])
    by_stage = {s.stage.value: s for s in out["stage_entries"]}
    assert by_stage["packaging"].packing_gain_kg == Decimal("1.500"), by_stage
    assert by_stage["packaging"].wastage_quantity == Decimal("0.000")
    assert by_stage["mixing"].packing_gain_kg == Decimal("0.000")
    assert by_stage["mixing"].wastage_quantity == Decimal("1.000")


def test_save_path_keeps_clamp_on_other_stages(db, setup):
    out = _produce(db, setup, [
        {"stage": "grinding", "input_quantity": 50, "output_quantity": 55},
    ])
    row = out["stage_entries"][0]
    assert row.packing_gain_kg == Decimal("0.000"), row
    assert row.wastage_quantity == Decimal("0.000")


def test_detail_endpoint_exposes_gain(db, setup):
    out = _produce(db, setup, [
        {"stage": "packaging", "input_quantity": 49, "output_quantity": 50.5},
    ])
    d = m.get_production_entry(out["id"], db)
    assert d["stage_entries"][0].packing_gain_kg == Decimal("1.500"), d
    # What the HTTP layer actually serves: the response schema must carry
    # the field, or response_model strips it back to a bare number view.
    from backend.app.schemas import ProductionStageSchema

    served = ProductionStageSchema.model_validate(d["stage_entries"][0])
    assert served.packing_gain_kg == Decimal("1.500"), served


def test_report_shows_gain_without_netting_loss(db, setup):
    _produce(db, setup, [
        {"stage": "grinding", "input_quantity": 50, "output_quantity": 45},
        {"stage": "packaging", "input_quantity": 45, "output_quantity": 46},
    ])
    rep = m.report_by_category(db)
    row = rep["rows"][0]
    # 5 kg of real loss stays 5 kg even with 1 kg of packing gain present.
    assert float(row["wastage_kg"]) == pytest.approx(5.0), row
    assert float(row["packing_gain_kg"]) == pytest.approx(1.0), row
    assert float(rep["totals"]["packing_gain_kg"]) == pytest.approx(1.0), rep


def test_migration_covers_new_column():
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "app" / "core" / "database.py").read_text(
        encoding="utf-8"
    )
    assert '("production_stage_entries", "packing_gain_kg"' in src, (
        "packing_gain_kg is missing from _ensure_columns: existing databases "
        "will crash with 'no such column' wherever stage entries are read"
    )


def test_production_view_shows_gain_column():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "production.html"), encoding="utf-8") as fh:
        html = fh.read()
    assert "packing_gain_kg" in html, "detail stages table has no gain column"


def test_category_report_shows_gain_column():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "reports.html"), encoding="utf-8") as fh:
        html = fh.read()
    assert "Packing gain" in html, "category summary hides the gain line"


def test_help_covers_packing_gain():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "help.html"), encoding="utf-8") as fh:
        html = fh.read()
    assert "Packing gain" in html, "help never explains the packaging surplus"
    assert "no longer exceeds recorded input" not in html, (
        "help still claims output can never exceed input"
    )
