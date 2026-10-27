"""Customer-payments importer (receipts for old/existing challans).

Run: python -m pytest backend/tests/test_customer_payments_import.py
"""

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app import models  # noqa: E402
from backend.app.services.excel_import import run_import  # noqa: E402

_seq = iter(range(1, 10000))


def _customer(db):
    c = models.Customer(
        customer_id=f"CUST-{next(_seq):04d}", name="Payee Traders",
        contact_number="9876500001",
    )
    db.add(c)
    db.commit()
    return c


def _challan(db, customer, number, total, status="confirmed"):
    from datetime import datetime

    ch = models.SalesChallan(
        challan_number=number, challan_date=datetime(2026, 7, 10),
        customer_id=customer.id, status=status,
        total_price=Decimal(str(total)), paid_amount=Decimal("0"),
        outstanding_amount=Decimal(str(total)),
    )
    db.add(ch)
    db.commit()
    db.refresh(ch)
    return ch


def test_part_payments_update_balances(db, make_db):
    _ = make_db
    c = _customer(db)
    _challan(db, c, "CH-PAY-1", 10000)
    out = run_import(db, "customer_payments", _xlsx([
        ["Challan Number", "Payment Date", "Amount", "Payment Mode"],
        ["CH-PAY-1", "2026-08-05", 4000, "Cash"],
        ["CH-PAY-1", "2026-08-20", 6000, "UPI"],
    ]))
    assert out["imported"] == 2, out
    ch = db.query(models.SalesChallan).filter(
        models.SalesChallan.challan_number == "CH-PAY-1").first()
    assert Decimal(str(ch.paid_amount)) == Decimal("10000")
    assert Decimal(str(ch.outstanding_amount)) == Decimal("0")
    assert str(getattr(ch.status, "value", ch.status)) == "paid"
    db.refresh(c)
    assert Decimal(str(c.outstanding_balance or 0)) == Decimal("0")
    pays = db.query(models.CustomerPayment).filter(
        models.CustomerPayment.challan_id == ch.id).order_by(
        models.CustomerPayment.payment_date).all()
    assert [str(p.payment_date)[:10] for p in pays] == ["2026-08-05", "2026-08-20"]


def test_overpayment_unknown_and_draft_rows_skipped(db, make_db):
    _ = make_db
    c = _customer(db)
    _challan(db, c, "CH-PAY-2", 5000)
    _challan(db, c, "CH-DRAFT-1", 5000, status="draft")
    out = run_import(db, "customer_payments", _xlsx([
        ["Challan Number", "Amount"],
        ["CH-PAY-2", 6000],
        ["CH-NOPE-9", 1000],
        ["CH-DRAFT-1", 1000],
        ["CH-PAY-2", 2000],
    ]))
    assert out["imported"] == 1, out
    assert out["skipped"] == 3, out
    reasons = " ".join(r["reason"] for r in out["skipped_rows"])
    assert "outstanding" in reasons and "not found" in reasons and "draft" in reasons
    ch = db.query(models.SalesChallan).filter(
        models.SalesChallan.challan_number == "CH-PAY-2").first()
    assert str(getattr(ch.status, "value", ch.status)) == "partially_paid"


def test_needs_challans_first(make_db):
    db = make_db()
    out = run_import(db, "customer_payments", _xlsx([
        ["Challan Number", "Amount"],
        ["CH-X-1", 100],
    ]))
    assert out["success"] is False
    assert "sales challans" in (out["warning"] or "")


def _xlsx(rows):
    import io

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
