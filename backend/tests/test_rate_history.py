"""Pack-size rate history.

Run: python -m pytest backend/tests/test_rate_history.py
"""

import os
import sys
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _req():
    return SimpleNamespace(query_params={"reason": "test change note"}, headers={}, client=None)


def _size(db):
    prod = models.Product(product_id="P-R1", name="Rate Masala")
    db.add(prod)
    db.flush()
    ps = models.PackagingSize(product_id=prod.id, size_name="250g",
                              weight_in_kg=Decimal("0.25"),
                              rate_per_kg=Decimal("200"), is_active=True)
    db.add(ps)
    db.commit()
    return ps


def test_rate_change_writes_history_row(db):
    ps = _size(db)
    m.update_packaging_size(ps.id, {"rate_per_kg": 220}, _req(), db)
    rows = m.size_rate_history(ps.id, db)
    assert len(rows) == 1
    assert float(rows[0]["old_rate"]) == 200.0
    assert float(rows[0]["new_rate"]) == 220.0


def test_unchanged_rate_writes_nothing(db):
    ps = _size(db)
    m.update_packaging_size(ps.id, {"rate_per_kg": 200}, _req(), db)
    assert m.size_rate_history(ps.id, db) == []
    m.update_packaging_size(ps.id, {"threshold_packets": 50}, _req(), db)
    assert m.size_rate_history(ps.id, db) == []


def test_history_lists_newest_first(db):
    ps = _size(db)
    m.update_packaging_size(ps.id, {"rate_per_kg": 210}, _req(), db)
    m.update_packaging_size(ps.id, {"rate_per_kg": 230}, _req(), db)
    rows = m.size_rate_history(ps.id, db)
    assert [float(r["new_rate"]) for r in rows] == [230.0, 210.0]
