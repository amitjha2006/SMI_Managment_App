"""Per-size finished-goods thresholds (packets).

Run: python -m pytest backend/tests/test_pack_threshold.py
"""

import os
import sys
from decimal import Decimal
from pathlib import Path
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
    mat = models.RawMaterial(
        material_id="RM-H1", name="Test Raw", unit="kg",
        current_stock=Decimal("10000"), purchase_rate=Decimal("100"),
    )
    db.add(mat)
    prod = models.Product(product_id="P-H1", name="Threshold Masala")
    db.add(prod)
    db.flush()
    big = models.PackagingSize(
        product_id=prod.id, size_name="250g", weight_in_kg=Decimal("0.25"),
        rate_per_kg=Decimal("200"), packets_per_unit=1, is_active=True,
        threshold_packets=Decimal("100"),
    )
    small = models.PackagingSize(
        product_id=prod.id, size_name="50g", weight_in_kg=Decimal("0.05"),
        rate_per_kg=Decimal("210"), packets_per_unit=1, is_active=True,
        threshold_packets=Decimal("0"),
    )
    db.add_all([big, small])
    db.commit()
    return {"mat": mat, "prod": prod, "big": big, "small": small}


def _produce(db, setup, size, packets):
    return m.create_production_entry({
        "product_id": setup["prod"].id,
        "production_date": "2026-09-15T10:00:00",
        "target_quantity": packets,
        "outputs": [{"packaging_size_id": size.id, "packets": packets}],
        "consumed_materials": [{"raw_material_id": setup["mat"].id, "quantity": 50}],
        "stages": [{"stage": "mixing", "input_quantity": 60, "output_quantity": 58}],
    }, _req(), db)


def test_schema_count_pins_are_current():
    """The suite pins several counts; this one pins the migration list so a
    future column addition cannot silently skip old databases again."""
    src = Path(__file__).resolve().parent.parent / "app" / "core" / "database.py"
    text = src.read_text(encoding="utf-8")
    assert '("packaging_sizes", "threshold_packets"' in text, (
        "threshold_packets is missing from _ensure_columns: existing databases "
        "will crash with 'no such column' on every finished-stock query"
    )
    assert '("packaging_sizes", "wrapper_weight_kg"' in text, (
        "wrapper_weight_kg is missing from _ensure_columns: existing databases "
        "will crash with 'no such column' wherever pack sizes are read"
    )
    assert '("packaging_sizes", "wrapper_material_id"' in text, (
        "wrapper_material_id is missing from _ensure_columns: existing databases "
        "will crash with 'no such column' wherever pack sizes are read"
    )


def test_below_threshold_alerts_above_does_not(db, setup):
    _produce(db, setup, setup["big"], 50)  # 50 < 100 -> low
    _produce(db, setup, setup["small"], 500)  # threshold 0, stocked -> quiet
    lows = m._low_finished_sizes(db)
    assert [low["size_name"] for low in lows] == ["250g"]
    assert lows[0]["packets"] == pytest.approx(50.0)
    assert lows[0]["threshold_packets"] == pytest.approx(100.0)
    assert lows[0]["product_name"] == "Threshold Masala"


def test_zero_threshold_alerts_only_when_empty(db, setup):  # noqa: ARG001 - setup fixture builds the products under test
    lows = m._low_finished_sizes(db)
    names = [low["size_name"] for low in lows]
    assert "50g" in names  # 0 packets <= 0 threshold
    assert "250g" in names


def test_retired_sizes_never_alert(db, setup):
    setup["big"].is_active = False
    db.commit()
    _produce(db, setup, setup["small"], 500)
    assert all(low["size_name"] != "250g" for low in m._low_finished_sizes(db))


def test_threshold_saved_via_api_and_negative_refused(db, setup):
    out = m.update_packaging_size(
        setup["small"].id, {"threshold_packets": 250}, _req(), db)
    assert Decimal(str(out.threshold_packets)) == Decimal("250")
    with pytest.raises(HTTPException) as e:
        m.update_packaging_size(setup["small"].id, {"threshold_packets": -1}, _req(), db)
    assert e.value.status_code == 400


def test_dashboard_and_summary_include_packs(db, setup):
    _produce(db, setup, setup["big"], 50)
    stats = m.get_dashboard_stats(db)
    assert stats["low_pack_alerts"] == 2  # 250g low + empty 50g
    assert stats["low_stock_items"] >= 2
    rep = m.reports_summary(None, None, db)
    assert any(p["name"].endswith("250g") for p in rep["low_packs"])
