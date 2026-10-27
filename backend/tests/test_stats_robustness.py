"""Dashboard stats must survive hostile legacy rows.

Real databases accumulate NULLs and half-written rows (89 pre-existing
challans predate several columns). get_dashboard_stats feeds the cards
AND the attention list, so one bad row must never 500 the whole thing.

Run: python -m pytest backend/tests/test_stats_robustness.py -v
"""

import os
import sys
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _customer(db):
    c = models.Customer(customer_id="CUST-H1", name="Hostile Co",
                        contact_number="9000000111")
    db.add(c)
    db.commit()
    return c


def test_nulls_everywhere_still_returns_stats(db):
    from sqlalchemy import text as _text

    c = _customer(db)
    ch1 = models.SalesChallan(
        challan_number="CH-H-1", challan_date=datetime.now(), customer_id=c.id,
        status=m.ChallanStatus.CONFIRMED,
        total_price=Decimal("100"), paid_amount=Decimal("0"),
        outstanding_amount=Decimal("100"))
    db.add(ch1)
    ch2 = models.SalesChallan(
        challan_number="CH-H-2", challan_date=datetime.now(),
        customer_id=c.id, status=m.ChallanStatus.CONFIRMED,
        total_price=Decimal("100"), paid_amount=None,
        outstanding_amount=None)
    db.add(ch2)
    s = models.Supplier(supplier_id="SUP-H1", name="Hostile Sup",
                        contact_number="9000000112")
    db.add(s)
    db.flush()
    inv = models.PurchaseInvoice(
        invoice_number="INV-H-1", invoice_date=datetime.now(), supplier_id=s.id,
        total_amount=Decimal("200"), paid_amount=Decimal("0"),
        outstanding_amount=Decimal("200"))
    db.add(inv)
    db.flush()
    db.add(models.CustomerPayment(
        challan_id=ch1.id, payment_date=datetime.now(), amount=Decimal("10"),
        payment_mode="cash"))
    db.add(models.SupplierPayment(
        purchase_invoice_id=inv.id, payment_date=datetime.now(),
        amount=Decimal("10"), payment_mode="cash"))
    cat = models.ExpenseCategory(name="Hostile", is_active=True)
    db.add(cat)
    db.flush()
    db.add(models.Expense(category_id=cat.id, description="h",
                          amount=Decimal("5"), date=datetime.now(),
                          payment_mode="cash"))
    db.commit()
    # Legacy corruption plain ORM writes cannot produce (defaults fill
    # Nones): NULL money figures from older code paths.
    db.execute(_text("UPDATE sales_challans SET paid_amount=NULL "
                     "WHERE challan_number='CH-H-2'"))
    db.execute(_text("UPDATE purchase_invoices SET paid_amount=NULL "
                     "WHERE invoice_number='INV-H-1'"))
    db.commit()
    stats = m.get_dashboard_stats(db)
    for key in ("receivables_aging", "receivables_overdue", "payables_aging",
                "payables_overdue", "cashflow_month", "cashflow_net",
                "ready_to_dispatch", "open_receivables"):
        assert key in stats, f"missing {key}"


def test_attention_failure_shows_the_real_reason():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "dashboard.html"), encoding="utf-8") as fh:
        dash = fh.read()
    i = dash.find("async function loadAttention()")
    assert i >= 0
    nxt = dash.find("\nasync function ", i + 1)
    body = dash[i:nxt if nxt > 0 else len(dash)]
    assert "e.message" in body, (
        "attention failure must surface the real reason, not just 'Could not load'"
    )
