"""Ready-to-dispatch: sold but still in the godown.

Confirmed (or paid) challans with no dispatch recorded. Drafts are not
ready (unconfirmed), closed cannot dispatch, archived/delivered are done.

Run: python -m pytest backend/tests/test_ready_to_dispatch.py -v
"""

import os
import sys
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _req():
    return SimpleNamespace(query_params={"reason": "test change note"}, headers={}, client=None)


def _setup(db):
    c = models.Customer(customer_id="CUST-D1", name="Dispatch Co",
                        contact_number="9000000071")
    db.add(c)
    prod = models.Product(product_id="P-D1", name="Dispatch Masala")
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


def _draft(db, s):
    ch = m.create_challan({
        "customer_id": s["c"].id,
        "challan_date": datetime.now().isoformat(),
        "items": [{"product_id": s["prod"].id,
                   "packaging_size_id": s["size"].id,
                   "quantity": 4, "weight": 1.0, "rate_per_kg": 200}],
    }, _req(), db)
    return db.query(models.SalesChallan).filter(
        models.SalesChallan.id == ch["id"]).one()


def test_confirmed_undispatched_counts(db):
    s = _setup(db)
    row = _draft(db, s)
    m.set_challan_status(row.id, {"status": "confirmed"}, _req(), db)
    assert m.get_dashboard_stats(db)["ready_to_dispatch"] == 1


def test_dispatched_delivered_draft_closed_archived_do_not_count(db):
    s = _setup(db)
    base = _draft(db, s)
    m.set_challan_status(base.id, {"status": "confirmed"}, _req(), db)

    disp = _draft(db, s)
    m.set_challan_status(disp.id, {"status": "confirmed"}, _req(), db)
    disp.dispatched_at = datetime.now()
    db.commit()

    done = _draft(db, s)
    m.set_challan_status(done.id, {"status": "confirmed"}, _req(), db)
    done.dispatched_at = datetime.now()
    done.delivered_at = datetime.now()
    db.commit()

    _draft(db, s)  # still a draft

    closed = _draft(db, s)
    m.set_challan_status(closed.id, {"status": "confirmed"}, _req(), db)
    m.set_challan_status(closed.id, {"status": "closed"}, _req(), db)

    arch = _draft(db, s)
    m.set_challan_status(arch.id, {"status": "confirmed"}, _req(), db)
    arch.is_archived = True
    db.commit()

    assert m.get_dashboard_stats(db)["ready_to_dispatch"] == 1


def test_dashboard_links_ready_to_dispatch():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "dashboard.html"), encoding="utf-8") as fh:
        dash = fh.read()
    assert "ready_to_dispatch" in dash, "dashboard ignores the ready count"
    assert "#/challans" in dash
