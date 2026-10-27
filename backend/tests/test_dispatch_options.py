"""Dispatch panel suggests transporters/vehicles used on past challans.

First entry is typed by hand; later challans pick from history via
datalists fed by GET /challans/dispatch-options.

Run: python -m pytest backend/tests/test_dispatch_options.py -v
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _customer(db):
    c = models.Customer(customer_id="CUST-DO1", name="Dispatch Co",
                        contact_number="9000000093")
    db.add(c)
    db.commit()
    return c.id


def _challan(db, customer_id, number, transporter=None, vehicle=None):
    db.add(models.SalesChallan(
        challan_number=number, challan_date=datetime(2026, 9, 10),
        customer_id=customer_id,
        transporter=transporter, vehicle_no=vehicle,
    ))
    db.commit()


def test_distinct_recent_first(db):
    cid = _customer(db)
    _challan(db, cid, "CH-O1", transporter="Old Trans", vehicle="MH-01-A")
    _challan(db, cid, "CH-O2", transporter="New Trans", vehicle="MH-02-B")
    _challan(db, cid, "CH-O3", transporter="New Trans", vehicle="MH-02-B")
    out = m.challan_dispatch_options(db)
    assert out["transporters"] == ["New Trans", "Old Trans"], out
    assert out["vehicles"] == ["MH-02-B", "MH-01-A"], out


def test_case_variants_dedupe_and_blanks_dropped(db):
    cid = _customer(db)
    _challan(db, cid, "CH-O1", transporter="ABC Transport", vehicle="MH-01-A")
    _challan(db, cid, "CH-O2", transporter="abc transport ", vehicle="  ")
    _challan(db, cid, "CH-O3", transporter=None, vehicle=None)
    out = m.challan_dispatch_options(db)
    # Most-recent casing wins; blanks and nulls never list.
    assert out["transporters"] == ["abc transport"], out
    assert out["vehicles"] == ["MH-01-A"], out


def test_dispatch_panel_has_datalists():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "challans.html"), encoding="utf-8") as fh:
        html = fh.read()
    assert 'list="disp-transporter-list"' in html
    assert 'list="disp-vehicle-list"' in html
    assert "/challans/dispatch-options" in html
