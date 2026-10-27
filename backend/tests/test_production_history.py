"""Production history: per-batch, per-material ledger.

Each row is one material in one batch: consumed kg, processed share
(consumed minus the batch's pro-rata stage loss, the same derivation as
the raw-material summary), wastage kg and %. All non-archived batches
count, newest first; negatives are shown honestly, never clamped.

Run: python -m pytest backend/tests/test_production_history.py -v
"""

import os
import sys
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402
from backend.app.models.finished_product import FinishedProductMovementType  # noqa: E402,E501
from backend.app.models.production import ProductionStage, ProductionStatus  # noqa: E402,E501


def _mat(db, code, name):
    mat = models.RawMaterial(
        material_id=code, name=name, unit="kg",
        opening_stock=Decimal("0"), current_stock=Decimal("1000"),
    )
    db.add(mat)
    db.commit()
    return mat


def _product(db):
    prod = models.Product(product_id="P-PH", name="PH Mix")
    db.add(prod)
    db.flush()
    size = models.PackagingSize(
        product_id=prod.id, size_name="250g", weight_in_kg=Decimal("0.25"),
        rate_per_kg=Decimal("200"), packets_per_unit=1, is_active=True,
    )
    db.add(size)
    db.commit()
    return prod, size


def _entry(db, prod, size, number, day, status, consumed, stages, archived=False):
    entry = models.ProductionEntry(
        production_number=number, production_date=datetime(2026, 9, day, 10, 0, 0),
        product_id=prod.id, packaging_size_id=size.id,
        target_quantity=Decimal("100"), actual_output=Decimal("90"),
        status=status, is_archived=archived,
    )
    db.add(entry)
    db.flush()
    for mid, qty in consumed:
        db.add(models.RawMaterialStockMovement(
            raw_material_id=mid, movement_type="consumption",
            reference_type="production_entry", reference_id=entry.id,
            quantity=Decimal(str(qty)), unit="kg", rate=Decimal("100"),
            created_at=datetime(2026, 9, day, 10, 0, 0),
        ))
        db.add(models.FinishedProductStockMovement(
            product_id=prod.id, packaging_size_id=size.id,
            movement_type=FinishedProductMovementType.PRODUCTION,
            reference_type="production_entry", reference_id=entry.id,
            quantity=Decimal(str(qty)), packets=100,
            created_at=datetime(2026, 9, day, 10, 0, 0),
        ))
    for i, (stage, in_q, out_q) in enumerate(stages, 1):
        db.add(models.ProductionStageEntry(
            production_entry_id=entry.id, stage=stage, sequence=i,
            input_quantity=Decimal(str(in_q)), output_quantity=Decimal(str(out_q)),
            wastage_quantity=Decimal(str(in_q - out_q)),
            wastage_percentage=Decimal("0"),
            created_at=datetime(2026, 9, day, 10, 0, 0),
        ))
    db.commit()
    return entry


def _seed(db):
    a = _mat(db, "RM-PHA", "PH Turmeric")
    b = _mat(db, "RM-PHB", "PH Coriander")
    prod, size = _product(db)
    _entry(db, prod, size, "PH-1", 10, ProductionStatus.COMPLETED,
           [(a.id, 80), (b.id, 20)], [(ProductionStage.ROASTING, 100, 96)])
    _entry(db, prod, size, "PH-2", 12, ProductionStatus.DRAFT,
           [(a.id, 50)], [(ProductionStage.GRINDING, 50, 48)])
    _entry(db, prod, size, "PH-3", 14, ProductionStatus.COMPLETED,
           [(b.id, 10)], [(ProductionStage.ROASTING, 10, 10)], archived=True)
    return a, b


def test_pro_rata_rows_newest_first(db):
    _seed(db)
    out = m.production_history(db, "2026-09-01", "2026-09-30")
    rows = out["rows"]
    assert [(r["production_number"], r["material"]) for r in rows] == [
        ("PH-2", "PH Turmeric"),
        ("PH-1", "PH Coriander"),
        ("PH-1", "PH Turmeric"),
    ]
    mix_a = rows[2]
    assert mix_a["consumed_kg"] == 80
    assert mix_a["wastage_kg"] == 3.2
    assert mix_a["processed_kg"] == 76.8
    assert mix_a["wastage_pct"] == 4.0
    assert rows[1]["wastage_kg"] == 0.8
    assert out["totals"]["batches"] == 2
    assert out["totals"]["consumed_kg"] == 150
    assert out["totals"]["wastage_kg"] == 6.0


def test_archived_batches_excluded(db):
    _seed(db)
    out = m.production_history(db, "2026-09-01", "2026-09-30")
    assert "PH-3" not in [r["production_number"] for r in out["rows"]]


def test_negative_wastage_shown_honestly(db):
    a = _mat(db, "RM-PHN", "PH Gain")
    prod, size = _product(db)
    entry = models.ProductionEntry(
        production_number="PH-G", production_date=datetime(2026, 9, 15, 10, 0, 0),
        product_id=prod.id, packaging_size_id=size.id,
        target_quantity=Decimal("100"), actual_output=Decimal("100"),
        status=ProductionStatus.COMPLETED, is_archived=False,
    )
    db.add(entry)
    db.flush()
    db.add(models.RawMaterialStockMovement(
        raw_material_id=a.id, movement_type="consumption",
        reference_type="production_entry", reference_id=entry.id,
        quantity=Decimal("2"), unit="kg", rate=Decimal("100"),
        created_at=datetime(2026, 9, 15, 10, 0, 0),
    ))
    db.add(models.ProductionStageEntry(
        production_entry_id=entry.id, stage=ProductionStage.MIXING, sequence=1,
        input_quantity=Decimal("2"), output_quantity=Decimal("2.5"),
        wastage_quantity=Decimal("-0.5"), wastage_percentage=Decimal("0"),
        created_at=datetime(2026, 9, 15, 10, 0, 0),
    ))
    db.commit()
    out = m.production_history(db, "2026-09-01", "2026-09-30")
    row = next(r for r in out["rows"] if r["production_number"] == "PH-G")
    assert row["wastage_kg"] == -0.5
    assert row["wastage_pct"] == -25.0


def test_production_history_page_pins():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "js", "app.js"), encoding="utf-8") as fh:
        assert "production-history.html" in fh.read(), "no history route"
    with open(os.path.join(root, "frontend", "index.html"), encoding="utf-8") as fh:
        index = fh.read()
    pi = index.find('href="#/production"')
    hi = index.find('href="#/production-history"')
    assert pi >= 0 and hi >= 0 and pi < hi, "not next to Production"
    path = os.path.join(root, "frontend", "pages", "production-history.html")
    with open(path, encoding="utf-8") as fh:
        html = fh.read()
    for token in ("/reports/production-history", "ph-search", "ph-status",
                  "sortValue", "phPreset"):
        assert token in html, f"page misses {token}"
