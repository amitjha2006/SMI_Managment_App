"""Challan list order: same-day challans newest-first by id.

The list and the sort dropdown compare only the challan date. Two challans
created on the same day (date-only input = midnight) tie, so the newest one
must win the tie by id — otherwise the latest challan of the day does not
show first.

Run: python -m pytest backend/tests/test_challan_list_order.py -v
"""

import os
import sys
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _req():
    return SimpleNamespace(headers={}, client=None)


def _setup(db):
    c = models.Customer(customer_id="CUST-O1", name="Order Co", contact_number="9000000021")
    db.add(c)
    prod = models.Product(product_id="P-O1", name="Order Masala")
    db.add(prod)
    db.flush()
    size = models.PackagingSize(
        product_id=prod.id, size_name="250g",
        weight_in_kg=Decimal("0.25"), rate_per_kg=Decimal("200"), is_active=True,
    )
    db.add(size)
    db.commit()
    return {"c": c, "prod": prod, "size": size}


def _make(db, s, date):
    return m.create_challan({
        "customer_id": s["c"].id,
        "challan_date": date,
        "items": [{
            "product_id": s["prod"].id,
            "packaging_size_id": s["size"].id,
            "quantity": 4, "weight": 1.0, "rate_per_kg": 200,
        }],
    }, _req(), db)


def test_same_day_challans_newest_first(db):
    s = _setup(db)
    first = _make(db, s, "2026-09-10T00:00:00")
    second = _make(db, s, "2026-09-10T00:00:00")
    rows = m.list_challans(db=db)
    ids = [r["id"] for r in rows]
    assert ids.index(second["id"]) < ids.index(first["id"])


def test_newer_day_still_sorts_first(db):
    s = _setup(db)
    old = _make(db, s, "2026-09-09T00:00:00")
    new = _make(db, s, "2026-09-10T00:00:00")
    rows = m.list_challans(db=db)
    ids = [r["id"] for r in rows]
    assert ids.index(new["id"]) < ids.index(old["id"])
