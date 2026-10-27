"""A cancelled production entry can be reopened (click-path finding CP-012).

"Cancel Entry" sat next to "Mark Complete" and was final: one mis-click left the
batch labelled cancelled for good. Nothing in the ledger moves on a status
change - finished stock and raw consumption are posted when the entry is
created - so reopening is a label change, and a draft can be completed again.

Pin: cancelled -> draft is allowed, completed -> draft is still refused, and
reopening posts no extra stock movements.

Run: python -m pytest backend/tests/test_production_cancel_reopen.py -v
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


def _status(value):
    return str(getattr(value, "value", value) or "").lower()


@pytest.fixture
def setup(db):
    mat = models.RawMaterial(
        material_id="RM-RO1", name="Reopen Raw", unit="kg",
        current_stock=Decimal("500"), purchase_rate=Decimal("100"),
    )
    prod = models.Product(product_id="P-RO1", name="Reopen Masala")
    db.add_all([mat, prod])
    db.flush()
    size = models.PackagingSize(
        product_id=prod.id, size_name="100g", weight_in_kg=Decimal("0.1"),
        rate_per_kg=Decimal("200"), packets_per_unit=1, is_active=True,
    )
    db.add(size)
    db.commit()
    return {"mat": mat, "prod": prod, "size": size}


def _entry(setup, db):
    return m.create_production_entry(
        {
            "product_id": setup["prod"].id,
            "packaging_size_id": setup["size"].id,
            "production_date": "2026-09-15T10:00:00",
            "target_quantity": 10,
            "consumed_materials": [
                {"raw_material_id": setup["mat"].id, "quantity": 10}
            ],
            "stages": [
                {"stage": "roasting", "input_quantity": 10, "output_quantity": 10},
                {"stage": "packaging", "input_quantity": 10, "output_quantity": 10},
            ],
            "outputs": [{"packaging_size_id": setup["size"].id, "packets": 100}],
        },
        _req(),
        db,
    )


def _movement_count(db):
    return (
        db.query(models.FinishedProductStockMovement).count(),
        db.query(models.RawMaterialStockMovement).count(),
    )


def test_cancelled_entry_can_be_reopened_as_draft(setup, db):
    entry_id = _entry(setup, db)["id"]
    m.set_production_status(
        entry_id, {"status": "cancelled"}, db
    )
    assert _status(db.get(models.ProductionEntry, entry_id).status) == "cancelled"

    before = _movement_count(db)
    out = m.set_production_status(
        entry_id, {"status": "draft"}, db
    )
    assert _status(out["status"]) == "draft"
    assert _status(db.get(models.ProductionEntry, entry_id).status) == "draft"
    assert _movement_count(db) == before, "reopening must not post stock movements"


def test_completed_entry_still_cannot_go_back_to_draft(setup, db):
    entry_id = _entry(setup, db)["id"]
    m.set_production_status(
        entry_id, {"status": "completed"}, db
    )
    with pytest.raises(HTTPException) as exc:
        m.set_production_status(
            entry_id, {"status": "draft"}, db
        )
    assert exc.value.status_code == 400
    assert "draft" in str(exc.value.detail).lower()


def test_reopened_entry_can_be_completed_again(setup, db):
    entry_id = _entry(setup, db)["id"]
    m.set_production_status(
        entry_id, {"status": "cancelled"}, db
    )
    m.set_production_status(entry_id, {"status": "draft"}, db)
    before = _movement_count(db)
    out = m.set_production_status(
        entry_id, {"status": "completed"}, db
    )
    assert _status(out["status"]) == "completed"
    assert _movement_count(db) == before, "status moves must not repost stock"


def test_production_page_offers_the_reopen_button():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "production.html"), encoding="utf-8") as fh:
        html = fh.read()
    assert "Reopen as draft" in html, "cancelled entries have no way back in the UI"
    assert "'draft')" in html, "reopen button no longer targets the draft status"
    assert "stay in the ledger" in html, (
        "cancel confirmation must say the stock is not reversed"
    )
