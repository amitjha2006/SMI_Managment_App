"""Overdue spotlight: dashboard stats report 30+ day dues with amounts.

The attention list can only nag about counts today; collections need the
money figure. get_dashboard_stats must expose receivables_overdue
{count, amount} for open (non-draft, outstanding) challans older than
30 days, so the dashboard can print "Rs X overdue 30+ days (N challans)".

Run: python -m pytest backend/tests/test_overdue_spotlight.py -v
"""

import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _req():
    return SimpleNamespace(query_params={"reason": "test change note"}, headers={}, client=None)


def _setup(db):
    c = models.Customer(customer_id="CUST-O1", name="Overdue Co",
                        contact_number="9000000051")
    db.add(c)
    prod = models.Product(product_id="P-O1", name="Overdue Masala")
    db.add(prod)
    db.flush()
    size = models.PackagingSize(product_id=prod.id, size_name="250g",
                                weight_in_kg=Decimal("0.25"),
                                rate_per_kg=Decimal("200"), is_active=True)
    db.add(size)
    db.commit()
    db.add(models.FinishedProductStockMovement(
        product_id=prod.id, packaging_size_id=size.id,
        movement_type="production", reference_type="test",
        reference_id=0, quantity=Decimal("100")))
    db.commit()
    return {"c": c, "prod": prod, "size": size}


def _confirmed(db, s, date_iso, total=2000):
    ch = m.create_challan({
        "customer_id": s["c"].id,
        "challan_date": date_iso,
        "items": [{"product_id": s["prod"].id,
                   "packaging_size_id": s["size"].id,
                   "quantity": 40, "weight": 10,
                   "rate_per_kg": Decimal(total) / Decimal("10")}],
    }, _req(), db)
    m.set_challan_status(ch["id"], {"status": "confirmed"}, _req(), db)
    return ch


def test_overdue_reports_old_dues_with_amount(db):
    s = _setup(db)
    old = (datetime.now() - timedelta(days=45)).isoformat()
    young = (datetime.now() - timedelta(days=5)).isoformat()
    _confirmed(db, s, old)
    _confirmed(db, s, young)
    stats = m.get_dashboard_stats(db)
    overdue = stats["receivables_overdue"]
    assert overdue["count"] == 1, overdue
    assert Decimal(str(overdue["amount"])) == Decimal("2000"), overdue


def test_fresh_dues_are_not_overdue(db):
    s = _setup(db)
    young = (datetime.now() - timedelta(days=5)).isoformat()
    _confirmed(db, s, young)
    overdue = m.get_dashboard_stats(db)["receivables_overdue"]
    assert overdue["count"] == 0, overdue
    assert Decimal(str(overdue["amount"])) == Decimal("0"), overdue


def test_drafts_never_count_as_overdue(db):
    s = _setup(db)
    old = (datetime.now() - timedelta(days=45)).isoformat()
    m.create_challan({
        "customer_id": s["c"].id,
        "challan_date": old,
        "items": [{"product_id": s["prod"].id,
                   "packaging_size_id": s["size"].id,
                   "quantity": 40, "weight": 10, "rate_per_kg": 200}],
    }, _req(), db)
    overdue = m.get_dashboard_stats(db)["receivables_overdue"]
    assert overdue["count"] == 0, overdue


def _read(rel):
    import os as _os
    root = _os.path.dirname(_os.path.dirname(_os.path.dirname(os.path.abspath(__file__))))
    with open(_os.path.join(root, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


def test_dashboard_spotlights_overdue_with_collect_link():
    dash = _read("frontend/pages/dashboard.html")
    assert "receivables_overdue" in dash, (
        "dashboard must read the overdue figure from stats"
    )
    assert "#/challans?overdue=1" in dash, (
        "overdue line must link straight to the overdue challans"
    )


def test_challans_page_honors_overdue_filter():
    ch = _read("frontend/pages/challans.html")
    assert "overdue=1" in ch, "challans page must recognize the overdue deep-link"
    assert "clearOverdueFilter" in ch, "overdue filter must offer a way back out"
