"""Email a sales invoice PDF to the customer.

From a challan's invoice: the PDF bytes go out as a real attachment
(same figures as the print view), defaulting to the customer's email
and failing clearly when no address exists. Explicit addresses validate
through the shared recipient rules.

Run: python -m pytest backend/tests/test_invoice_email.py -v
"""

import os
import sys
from datetime import datetime
from decimal import Decimal
from email import message_from_string
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _req():
    return SimpleNamespace(headers={}, client=None)


def _configure(db):
    m._set_setting(db, "email.smtp_host", "smtp.example.com")
    m._set_setting(db, "email.username", "shop@example.com")
    m._set_setting(db, "email.password", "secret")
    db.commit()


class _FakeSMTP:
    instances = []

    def __init__(self, *_a, **_k):
        self.body = None
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, *_a):
        pass

    def sendmail(self, _sender, recipients, body):
        self.recipients = recipients
        self.body = body
        return {}


def _invoice(db, email="buyer@example.com"):
    c = models.Customer(customer_id="C-IE", name="Invoice Emailee",
                        contact_number="9000000001", email=email)
    db.add(c)
    db.commit()
    db.add(
        models.SalesChallan(
            challan_number="CH-IE1", challan_date=datetime(2026, 9, 10, 10, 0, 0),
            customer_id=c.id, status="confirmed",
            total_price=Decimal("1000"), paid_amount=Decimal("0"),
            outstanding_amount=Decimal("1000"),
        )
    )
    db.commit()
    ch = db.query(models.SalesChallan).filter(
        models.SalesChallan.challan_number == "CH-IE1").one()
    db.add(
        models.SalesInvoice(
            invoice_number="INV-IE1", invoice_date=datetime(2026, 9, 10, 10, 0, 0),
            challan_id=ch.id, customer_id=c.id,
            grand_total=Decimal("1000"),
        )
    )
    db.commit()
    return db.query(models.SalesInvoice).filter(
        models.SalesInvoice.invoice_number == "INV-IE1").one()


def _attachment_parts(body):
    msg = message_from_string(body)
    return [
        (part.get_filename(), part.get_payload(decode=True) or b"")
        for part in msg.walk()
        if part.get_content_disposition() == "attachment"
    ]


def test_invoice_pdf_goes_out_as_attachment(db, monkeypatch):
    _configure(db)
    inv = _invoice(db)
    _FakeSMTP.instances.clear()
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    out = m.send_invoice_email(inv.id, {"to": "buyer@example.com"}, _req(), db)
    files = _attachment_parts(_FakeSMTP.instances[-1].body)
    assert len(files) == 1, files
    name, payload = files[0]
    assert name and name.endswith(".pdf") and "INV-IE1" in name, name
    assert payload[:4] == b"%PDF", payload[:20]
    assert out["to"] == ["buyer@example.com"], out


def test_recipient_defaults_to_customer_email(db, monkeypatch):
    _configure(db)
    inv = _invoice(db)
    _FakeSMTP.instances.clear()
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    out = m.send_invoice_email(inv.id, {}, _req(), db)
    assert out["to"] == ["buyer@example.com"], out


def test_missing_address_fails_clearly(db, monkeypatch):
    _configure(db)
    inv = _invoice(db, email=None)
    _FakeSMTP.instances.clear()
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    with pytest.raises(HTTPException) as e:
        m.send_invoice_email(inv.id, {}, _req(), db)
    assert e.value.status_code == 400
    assert "email" in str(e.value.detail).lower(), e.value.detail
    assert not _FakeSMTP.instances


def test_bad_explicit_address_fails_fast(db, monkeypatch):
    _configure(db)
    inv = _invoice(db)
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    with pytest.raises(HTTPException) as e:
        m.send_invoice_email(inv.id, {"to": "not-an-address"}, _req(), db)
    assert e.value.status_code == 400


def test_invoice_dict_carries_customer_email_for_prefill(db):
    _configure(db)
    inv = _invoice(db)
    d = m._invoice_to_dict(inv)
    assert d.get("customer_email") == "buyer@example.com", d
