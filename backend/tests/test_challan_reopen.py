"""Reopen a closed challan: the close is undone, figures untouched.

Close is terminal, so a closed-by-mistake challan is stuck. Reopen restores
the status the books imply (never posted -> draft, posted unpaid ->
confirmed, partly paid -> partially_paid, fully paid -> paid) without moving
any stock or money.

Run: python -m pytest backend/tests/test_challan_reopen.py -v
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
    c = models.Customer(customer_id="CUST-R1", name="Reopen Co",
                        contact_number="9000000041")
    db.add(c)
    prod = models.Product(product_id="P-R1", name="Reopen Masala")
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


def _draft(db, setup, total=2000):
    ch = m.create_challan({
        "customer_id": setup["c"].id,
        "challan_date": "2026-09-10T10:00:00",
        "items": [{"product_id": setup["prod"].id,
                   "packaging_size_id": setup["size"].id,
                   "quantity": 40, "weight": 10,
                   "rate_per_kg": Decimal(total) / Decimal("10")}],
    }, _req(), db)
    return db.query(models.SalesChallan).filter(
        models.SalesChallan.id == ch["id"]).one()


def _close(db, row):
    m.set_challan_status(row.id, {"status": "closed"}, _req(), db)
    db.refresh(row)
    assert _st(row) == "closed"


def test_draft_closed_reopens_to_draft_and_edits_again(db, setup):
    row = _draft(db, setup)
    _close(db, row)
    out = m.reopen_challan(row.id, _req(), db)
    assert _st(row) == "draft"
    assert out["status"] in ("draft", m.ChallanStatus.DRAFT)
    # Editable again: the draft rules apply once more.
    m.update_draft_challan(row.id, {"notes": "after reopen"}, _req(), db)
    assert row.notes == "after reopen"


def test_confirmed_closed_reopens_to_confirmed(db, setup):
    row = _draft(db, setup)
    m.set_challan_status(row.id, {"status": "confirmed"}, _req(), db)
    _close(db, row)
    m.reopen_challan(row.id, _req(), db)
    assert _st(row) == "confirmed"
    assert Decimal(str(row.outstanding_amount)) == Decimal("2000")


def test_paid_closed_reopens_to_paid_with_figures_intact(db, setup):
    row = _draft(db, setup)
    m.set_challan_status(row.id, {"status": "confirmed"}, _req(), db)
    m.create_payment({"challan_id": row.id, "amount": 2000,
                      "payment_date": "2026-09-12T10:00:00",
                      "payment_mode": "cash"}, _req(), db)
    _close(db, row)
    m.reopen_challan(row.id, _req(), db)
    assert _st(row) == "paid"
    assert Decimal(str(row.paid_amount)) == Decimal("2000")
    assert Decimal(str(row.outstanding_amount)) == Decimal("0")


def test_partially_paid_closed_reopens_to_partially_paid(db, setup):
    row = _draft(db, setup)
    m.set_challan_status(row.id, {"status": "confirmed"}, _req(), db)
    m.create_payment({"challan_id": row.id, "amount": 500,
                      "payment_date": "2026-09-12T10:00:00",
                      "payment_mode": "cash"}, _req(), db)
    _close(db, row)
    m.reopen_challan(row.id, _req(), db)
    assert _st(row) == "partially_paid"
    assert Decimal(str(row.outstanding_amount)) == Decimal("1500")


def test_reopen_refused_when_not_closed(db, setup):
    row = _draft(db, setup)
    with pytest.raises(HTTPException) as e:
        m.reopen_challan(row.id, _req(), db)
    assert e.value.status_code == 400


def test_reopen_refused_when_archived(db, setup):
    row = _draft(db, setup)
    _close(db, row)
    row.is_archived = True
    db.commit()
    with pytest.raises(HTTPException) as e:
        m.reopen_challan(row.id, _req(), db)
    assert e.value.status_code == 409
