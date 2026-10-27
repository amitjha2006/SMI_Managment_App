"""Payment received summary: per-customer receipts in range.

One row per active customer (zeros included): receipt count plus received
amount. Only non-archived receipts inside the range count; archived and
out-of-range rows never move the totals.

Run: python -m pytest backend/tests/test_payment_summary.py -v
"""

import os
import sys
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _cust(db, code, name):
    c = models.Customer(customer_id=code, name=name, contact_number="9000000001")
    db.add(c)
    db.commit()
    return c


def _challan(db, cust, number, day):
    ch = models.SalesChallan(
        challan_number=number, challan_date=datetime(2026, 9, day, 10, 0, 0),
        customer_id=cust.id, status=m.ChallanStatus.CONFIRMED,
        total_price=Decimal("1000"), paid_amount=Decimal("0"),
        outstanding_amount=Decimal("1000"),
    )
    db.add(ch)
    db.commit()
    return ch


def _pay(db, challan, day, amount, archived=False):
    p = models.CustomerPayment(
        challan_id=challan.id, payment_date=datetime(2026, 9, day, 10, 0, 0),
        amount=Decimal(str(amount)), payment_mode="cash", is_archived=archived,
    )
    db.add(p)
    db.commit()
    return p


def _seed(db):
    a = _cust(db, "CUST-PRA", "PR Alice")
    b = _cust(db, "CUST-PRB", "PR Bob")
    _cust(db, "CUST-PRC", "PR Idle")
    ca = _challan(db, a, "PR-CH-1", 5)
    cb = _challan(db, b, "PR-CH-2", 6)
    _pay(db, ca, 7, 400)
    _pay(db, ca, 8, 100)
    _pay(db, cb, 9, 250, archived=True)
    old = _challan(db, b, "PR-CH-OLD", 5)
    old.challan_date = datetime(2026, 8, 5, 10, 0, 0)
    db.commit()
    po = _pay(db, old, 6, 999)
    po.payment_date = datetime(2026, 8, 6, 10, 0, 0)
    db.commit()
    # payment itself out of range (backdated receipt on an old challan)
    ancient = _challan(db, a, "PR-CH-ANC", 5)
    ancient.challan_date = datetime(2026, 8, 5, 10, 0, 0)
    db.commit()
    p = _pay(db, ancient, 7, 50)
    p.payment_date = datetime(2026, 8, 7, 10, 0, 0)
    db.commit()


def test_in_range_only_with_zeros(db):
    _seed(db)
    out = m.payment_summary(db, "2026-09-01", "2026-09-30")
    rows = {r["name"]: r for r in out["rows"]}
    assert rows["PR Alice"]["receipts"] == 2
    assert rows["PR Alice"]["received_amount"] == 500
    assert rows["PR Bob"]["receipts"] == 0
    assert rows["PR Bob"]["received_amount"] == 0
    assert rows["PR Idle"]["receipts"] == 0
    assert out["totals"]["receipts"] == 2
    assert out["totals"]["received_amount"] == 500
    assert out["totals"]["by_mode"] == {"cash": 500}


def test_register_lists_receipts_newest_first(db):
    _seed(db)
    out = m.payment_summary(db, "2026-09-01", "2026-09-30")
    reg = out["receipts"]
    assert len(reg) == 2
    assert reg[0]["amount"] == 100
    assert reg[1]["amount"] == 400
    assert reg[0]["customer_name"] == "PR Alice"
    assert reg[0]["challan_number"] == "PR-CH-1"
    assert reg[0]["payment_mode"] == "cash"


def test_payment_summary_page_pins():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "js", "app.js"), encoding="utf-8") as fh:
        assert "payment-summary.html" in fh.read(), "no payment summary route"
    with open(os.path.join(root, "frontend", "index.html"), encoding="utf-8") as fh:
        index = fh.read()
    pi = index.find('href="#/purchase-summary"')
    yi = index.find('href="#/payment-summary"')
    assert pi >= 0 and yi >= 0 and pi < yi, "not below Purchase Summary"
    assert 'data-group="insights"' in index, "fold group missing"
    path = os.path.join(root, "frontend", "pages", "payment-summary.html")
    with open(path, encoding="utf-8") as fh:
        html = fh.read()
    for token in ("/reports/payment-summary", "prsumPreset", "loadPaymentSummary"):
        assert token in html, f"page misses {token}"
