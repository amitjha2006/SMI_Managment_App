"""Raw-material Watch toggle for low-stock noise.

A zero-stock material that is not tracked must stay out of Attention
Required, the snapshot badges/counts, and every low-stock list — while
staying fully usable in purchases, production, and pickers (unlike
archiving, which removes it everywhere). watch_stock defaults ON so
existing behaviour is unchanged until someone unticks a material.

Run: python -m pytest backend/tests/test_raw_watch.py -v
"""

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _mat(db, code, name, opening=0, threshold=0, watch=True):
    mat = models.RawMaterial(
        material_id=code, name=name, unit="kg",
        opening_stock=Decimal(str(opening)), current_stock=Decimal(str(opening)),
        threshold_stock=Decimal(str(threshold)), watch_stock=watch,
    )
    db.add(mat)
    db.commit()
    return mat


def test_new_materials_default_to_watched(db):
    mat = _mat(db, "RM-W1", "Watched Seed")
    assert mat.watch_stock is True
    out = m.get_dashboard_stats(db)
    assert out["low_stock_items"] >= 1  # watched zero-stock still alerts


def test_unwatched_zero_stock_leaves_every_low_stock_path(db):
    _mat(db, "RM-W2", "Ignored Seed", opening=0, threshold=0, watch=False)

    stats = m.get_dashboard_stats(db)
    assert stats["low_stock_items"] == 0, stats

    rep = m.reports_summary(None, None, db)
    assert [r["name"] for r in rep["low_stock"]] == [], rep["low_stock"]

    _title, text, _html = m._build_daily_report(db, sections=["low_stock"])
    assert "Ignored Seed" not in text, text


def test_watched_zero_stock_still_alerts(db):
    _mat(db, "RM-W3", "Tracked Seed", opening=0, threshold=5, watch=True)

    assert m.get_dashboard_stats(db)["low_stock_items"] == 1
    rep = m.reports_summary(None, None, db)
    assert [r["name"] for r in rep["low_stock"]] == ["Tracked Seed"]
    assert "Tracked Seed" in m._build_daily_report(db, sections=["low_stock"])[1]


def test_put_toggle_flips_alerts(db):
    from types import SimpleNamespace

    from backend.app.schemas import RawMaterialUpdate

    mat = _mat(db, "RM-W4", "Toggle Seed", opening=0, threshold=0, watch=True)
    assert m.get_dashboard_stats(db)["low_stock_items"] == 1

    req = SimpleNamespace(headers={}, client=None, query_params={"reason": "toggle watch"})
    m.update_raw_material(mat.id, RawMaterialUpdate(watch_stock=False), req, db)
    assert m.get_dashboard_stats(db)["low_stock_items"] == 0

    m.update_raw_material(mat.id, RawMaterialUpdate(watch_stock=True), req, db)
    assert m.get_dashboard_stats(db)["low_stock_items"] == 1


def test_unwatched_material_stays_usable(db):
    mat = _mat(db, "RM-W5", "Usable Seed", opening=10, threshold=0, watch=False)
    rows = m.list_raw_materials(db)
    assert mat.id in [r.id for r in rows]
    assert m.get_dashboard_stats(db)["low_stock_items"] == 0
