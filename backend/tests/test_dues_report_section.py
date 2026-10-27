"""Dues follow-up as a daily-report section.

The morning mail can include the call list: one line per owing customer
(total, bills, days waiting), longest waiting first - the same rows as
the follow-up endpoint. Deselected, the section stays out; with no dues
it says so instead of mailing a blank.

Run: python -m pytest backend/tests/test_dues_report_section.py -v
"""

import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _owing_customer(db):
    c = models.Customer(customer_id="C-R1", name="Report Ower",
                        contact_number="9000000001")
    db.add(c)
    db.commit()
    db.add(
        models.SalesChallan(
            challan_number="CH-R1",
            challan_date=datetime.now() - timedelta(days=20),
            customer_id=c.id,
            status="confirmed",
            total_price=Decimal("1500"),
            paid_amount=Decimal("0"),
            outstanding_amount=Decimal("1500"),
        )
    )
    db.commit()


def test_dues_section_lists_owing_customers(db):
    _owing_customer(db)
    _subject, text, html = m._build_daily_report(db, ["dues"])
    assert "Report Ower" in text, text
    assert "1500" in text, text
    assert "20" in text, text
    assert "Report Ower" in html, html


def test_dues_section_stays_out_when_deselected(db):
    _owing_customer(db)
    _subject, text, _html = m._build_daily_report(db, ["stock"])
    assert "Report Ower" not in text, text
    assert "Dues follow-up" not in text, text


def test_dues_section_says_so_when_nothing_overdue(db):
    _subject, text, _html = m._build_daily_report(db, ["dues"])
    assert "nothing overdue" in text.lower(), text


def test_dues_is_an_offered_section():
    assert "dues" in m.REPORT_SECTION_CHOICES, m.REPORT_SECTION_CHOICES


def test_dues_section_toggle_exists_in_settings():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "settings.html"), encoding="utf-8") as fh:
        html = fh.read()
    assert '"dues"' in html, "dues section has no toggle on the Settings page"
