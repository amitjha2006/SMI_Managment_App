"""Dues follow-up list: one row per owing customer, longest waiting first.

The daily call list: every customer with open (non-draft, outstanding)
challans, with total due, oldest unpaid challan date, days waiting and
challan count. Paid-up, draft-only and zero-due customers stay out.
Same computation as the per-customer statement, aggregated one level up.

Run: python -m pytest backend/tests/test_dues_followup.py -v
"""

import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _customer(db, code, name, contact="9000000001"):
    c = models.Customer(customer_id=code, name=name, contact_number=contact)
    db.add(c)
    db.commit()
    return c


def _challan(db, customer, number, days_ago, outstanding, status="confirmed"):
    db.add(
        models.SalesChallan(
            challan_number=number,
            challan_date=datetime.now() - timedelta(days=days_ago),
            customer_id=customer.id,
            status=status,
            total_price=Decimal(str(outstanding)),
            paid_amount=Decimal("0"),
            outstanding_amount=Decimal(str(outstanding)),
        )
    )
    db.commit()


def test_owing_customers_longest_waiting_first(db):
    old = _customer(db, "C-D1", "Slow Payer")
    _challan(db, old, "CH-D1", 45, 5000)
    _challan(db, old, "CH-D2", 10, 1000)
    new = _customer(db, "C-D2", "Recent Ower")
    _challan(db, new, "CH-D3", 5, 2000)

    rows = m.dues_followup(db=db)

    assert [r["customer_name"] for r in rows] == ["Slow Payer", "Recent Ower"], rows
    first = rows[0]
    assert first["contact_number"] == "9000000001"
    assert Decimal(str(first["total_outstanding"])) == Decimal("6000")
    assert first["challan_count"] == 2
    assert first["max_age_days"] == 45, first


def test_paid_draft_and_zero_due_stay_out(db):
    c = _customer(db, "C-D3", "Settled Sam")
    _challan(db, c, "CH-D4", 60, 0)
    _challan(db, c, "CH-D5", 60, 3000, status="draft")
    rows = m.dues_followup(db=db)
    assert all(r["customer_name"] != "Settled Sam" for r in rows), rows
