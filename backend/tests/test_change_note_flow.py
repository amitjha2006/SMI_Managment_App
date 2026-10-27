"""Functional coverage for the mandatory change note on money endpoints.

The scan test (test_edit_reason_required.py) pins that only money-bearing
endpoints call _change_reason; these tests pin the behaviour itself:
without a note a change is refused, with one it goes through, and the
note lands in the audit trail next to who and when.

Run: python -m pytest backend/tests/test_change_note_flow.py -v
"""

import os
import sys
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _req(reason=None):
    qp = {"reason": reason} if reason else {}
    return SimpleNamespace(headers={}, client=None, query_params=qp)


def _customer(db, name="Note Customer"):
    c = models.Customer(customer_id="C-NOTE", name=name, contact_number="9000000001")
    db.add(c)
    db.commit()
    return c


def _draft_challan(db, customer):
    row = models.SalesChallan(
        challan_number="CH-NOTE-1", challan_date=datetime(2026, 10, 15, 10, 0, 0),
        customer_id=customer.id, status=m.ChallanStatus.DRAFT,
    )
    db.add(row)
    db.commit()
    return row


def _confirmed_challan_with_payment(db, customer):
    ch = _draft_challan(db, customer)
    ch.status = m.ChallanStatus.CONFIRMED
    ch.total_price = Decimal("100")
    ch.outstanding_amount = Decimal("50")
    db.flush()
    p = models.CustomerPayment(
        challan_id=ch.id, payment_date=datetime(2026, 10, 15, 11, 0, 0),
        amount=Decimal("50"), payment_mode="cash",
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def test_edit_refused_without_note(db):
    p = _confirmed_challan_with_payment(db, _customer(db))
    with pytest.raises(HTTPException) as err:
        m.update_payment(p.id, {"amount": "70"}, _req(), db)
    assert err.value.status_code == 400
    db.rollback()
    db.refresh(p)
    assert p.amount == Decimal("50"), "the edit must not have applied"


def test_edit_refused_below_min_length(db):
    p = _confirmed_challan_with_payment(db, _customer(db))
    for note in ("ok", "x", "  ha  "):
        with pytest.raises(HTTPException):
            m.update_payment(p.id, {"amount": "70", "reason": note}, _req(), db)
    db.rollback()


def test_edit_with_note_succeeds_and_audits(db):
    p = _confirmed_challan_with_payment(db, _customer(db))
    # Query-param note covers both validation and the audit append.
    m.update_payment(
        p.id, {"amount": "70"}, _req(reason="wrong amount entered"), db
    )
    logs = (
        db.query(models.AuditLog)
        .filter(models.AuditLog.record_id == p.id, models.AuditLog.action == "update")
        .all()
    )
    assert logs, "edit not audited"
    assert any("wrong amount entered" in (a.notes or "") for a in logs), [
        a.notes for a in logs
    ]


def test_archive_refused_without_note(db):
    p = _confirmed_challan_with_payment(db, _customer(db))
    with pytest.raises(HTTPException) as err:
        m.delete_payment(p.id, _req(), db)
    assert err.value.status_code == 400
    db.rollback()
    db.refresh(p)
    assert p.is_archived is False, "archived without a note"


def test_archive_with_note_records_it(db):
    p = _confirmed_challan_with_payment(db, _customer(db))
    m.delete_payment(p.id, _req(reason="duplicate receipt"), db)
    db.refresh(p)
    assert p.is_archived is True
    log = (
        db.query(models.AuditLog)
        .filter(models.AuditLog.record_id == p.id, models.AuditLog.action == "archive")
        .order_by(models.AuditLog.id.desc())
        .first()
    )
    assert log is not None and "duplicate receipt" in (log.notes or ""), log.notes


def test_payment_edit_requires_note(db):
    ch = _draft_challan(db, _customer(db))
    ch.status = m.ChallanStatus.CONFIRMED
    ch.total_price = Decimal("100")
    ch.outstanding_amount = Decimal("50")
    p = models.CustomerPayment(
        challan_id=ch.id, payment_date=datetime(2026, 10, 15, 11, 0, 0),
        amount=Decimal("50"), payment_mode="cash",
    )
    db.add(p)
    db.commit()
    with pytest.raises(HTTPException) as err:
        m.update_payment(p.id, {"amount": "70"}, _req(), db)
    assert err.value.status_code == 400
