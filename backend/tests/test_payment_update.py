"""Correct a challan payment in place: balances follow the delta.

Archiving never reverses money (spec 17), so a wrongly entered receipt is
fixed with PUT /payments/{id}, which reverses the old amount out of the
challan and customer balances before applying the new one.

Run: python -m pytest backend/tests/test_payment_update.py -v
"""

import os
import sys
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _req():
    return SimpleNamespace(query_params={"reason": "test change note"}, headers={}, client=None)


def _st(row):
    return str(getattr(row.status, "value", row.status) or "")


@pytest.fixture
def setup(db):
    c = models.Customer(customer_id="CUST-PU", name="Pay Update Co",
                        contact_number="9000000042")
    db.add(c)
    prod = models.Product(product_id="P-PU", name="Pay Update Masala")
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


def _confirmed(db, setup, total=2000):
    ch = m.create_challan({
        "customer_id": setup["c"].id,
        "challan_date": "2026-09-10T10:00:00",
        "items": [{"product_id": setup["prod"].id,
                   "packaging_size_id": setup["size"].id,
                   "quantity": 40, "weight": 10,
                   "rate_per_kg": Decimal(total) / Decimal("10")}],
    }, _req(), db)
    row = db.query(models.SalesChallan).filter(
        models.SalesChallan.id == ch["id"]).one()
    m.set_challan_status(row.id, {"status": "confirmed"}, _req(), db)
    db.refresh(row)
    return row


def _pay(db, row, amount):
    p = m.create_payment({"challan_id": row.id, "amount": amount,
                          "payment_date": "2026-09-12T10:00:00",
                          "payment_mode": "cash"}, _req(), db)
    db.refresh(row)
    return p


def test_update_amount_moves_challan_and_customer_balances(db, setup):
    row = _confirmed(db, setup)
    p = _pay(db, row, 500)
    out = m.update_payment(p["id"], {"amount": 800}, _req(), db)
    assert Decimal(str(out["amount"])) == Decimal("800")
    db.refresh(row)
    assert Decimal(str(row.paid_amount)) == Decimal("800")
    assert Decimal(str(row.outstanding_amount)) == Decimal("1200")
    assert _st(row) == "partially_paid"


def test_update_to_full_marks_paid(db, setup):
    row = _confirmed(db, setup)
    p = _pay(db, row, 500)
    m.update_payment(p["id"], {"amount": 2000}, _req(), db)
    db.refresh(row)
    assert _st(row) == "paid"
    assert Decimal(str(row.outstanding_amount)) == Decimal("0")


def test_update_refused_beyond_headroom(db, setup):
    row = _confirmed(db, setup)
    p = _pay(db, row, 500)
    with pytest.raises(HTTPException) as e:
        m.update_payment(p["id"], {"amount": 2500}, _req(), db)
    assert e.value.status_code == 400
    db.refresh(row)
    assert Decimal(str(row.paid_amount)) == Decimal("500")


def test_update_refused_when_archived(db, setup):
    row = _confirmed(db, setup)
    p = _pay(db, row, 500)
    rec = db.query(models.CustomerPayment).filter(
        models.CustomerPayment.id == p["id"]).one()
    rec.is_archived = True
    db.commit()
    with pytest.raises(HTTPException) as e:
        m.update_payment(p["id"], {"amount": 600}, _req(), db)
    assert e.value.status_code == 409
