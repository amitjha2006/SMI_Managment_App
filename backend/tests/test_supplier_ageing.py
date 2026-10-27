"""Supplier ageing: oldest-unpaid dues with amounts, like receivables.

get_dashboard_stats must expose payables_aging {d30, d60, over60} plus
payables_overdue {count, amount} over non-archived purchase invoices with
outstanding > 0 (PENDING and PARTIAL alike), so the dashboard can print
"Rs X overdue 30+ days" next to the pending purchase invoices.

Run: python -m pytest backend/tests/test_supplier_ageing.py -v
"""

import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _supplier(db):
    s = models.Supplier(supplier_id="SUP-A1", name="Ageing Suppliers",
                        contact_number="9000000101")
    db.add(s)
    db.commit()
    return s


_inv_seq = iter(range(1, 100000))


def _invoice(db, s, date_iso, total=3000, paid=0):
    inv = models.PurchaseInvoice(
        invoice_number=f"INV-A-{next(_inv_seq):05d}",
        invoice_date=datetime.fromisoformat(date_iso),
        supplier_id=s.id, total_amount=Decimal(str(total)),
        paid_amount=Decimal(str(paid)),
        outstanding_amount=Decimal(str(total)) - Decimal(str(paid)))
    db.add(inv)
    db.commit()
    return inv


def test_payables_ageing_buckets_and_overdue(db):
    s = _supplier(db)
    _invoice(db, s, (datetime.now() - timedelta(days=75)).isoformat())
    _invoice(db, s, (datetime.now() - timedelta(days=10)).isoformat(), total=1000)
    stats = m.get_dashboard_stats(db)
    assert stats["payables_aging"] == {"d30": 1, "d60": 0, "over60": 1}, stats
    overdue = stats["payables_overdue"]
    assert overdue["count"] == 1, overdue
    assert Decimal(str(overdue["amount"])) == Decimal("3000"), overdue


def test_paid_and_archived_invoices_stay_out(db):
    s = _supplier(db)
    old = (datetime.now() - timedelta(days=75)).isoformat()
    inv = _invoice(db, s, old, total=3000, paid=3000)
    assert Decimal(str(inv.outstanding_amount)) == Decimal("0")
    arch = _invoice(db, s, old)
    arch.is_archived = True
    db.commit()
    stats = m.get_dashboard_stats(db)
    assert stats["payables_aging"] == {"d30": 0, "d60": 0, "over60": 0}, stats
    assert stats["payables_overdue"] == {"count": 0, "amount": 0.0}, stats


def test_dashboard_shows_supplier_ageing():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "dashboard.html"), encoding="utf-8") as fh:
        dash = fh.read()
    assert "payables_aging" in dash, "dashboard ignores supplier ageing"
    assert "payables_overdue" in dash, "dashboard ignores supplier overdue figure"
