"""Multiple email recipients: comma text splits into real addresses.

The report/alert address boxes are free text, so "a@x, b@y" arrived at
_send_email as one string and went out as a single recipient - which
Gmail rejects with 553 as "not a valid RFC 5321 address". A lone
string now splits on commas/semicolons, and a malformed entry fails
fast naming itself instead of surfacing Gmail's cryptic 502.

Run: python -m pytest backend/tests/test_email_recipients.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import backend.main as m  # noqa: E402


def _configure(db):
    m._set_setting(db, "email.smtp_host", "smtp.example.com")
    m._set_setting(db, "email.username", "shop@example.com")
    m._set_setting(db, "email.password", "secret")
    db.commit()


class _FakeSMTP:
    instances = []

    def __init__(self, *_a, **_k):
        self.recipients = None
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, *a):
        pass

    def sendmail(self, _sender, recipients, _body):
        self.recipients = recipients
        return {}


def test_comma_text_sends_to_each_address(db, monkeypatch):
    _configure(db)
    _FakeSMTP.instances.clear()
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    out = m._send_email(db, "a@example.com, b@example.com", "Hi", "Hello")
    assert out["to"] == ["a@example.com", "b@example.com"], out
    assert _FakeSMTP.instances[-1].recipients == ["a@example.com", "b@example.com"]


def test_semicolons_and_spaces_split_cleanly(db, monkeypatch):
    _configure(db)
    _FakeSMTP.instances.clear()
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    out = m._send_email(db, "a@example.com; b@example.com ;", "Hi", "Hello")
    assert out["to"] == ["a@example.com", "b@example.com"], out


def test_malformed_entry_fails_fast_naming_it(db, monkeypatch):
    _configure(db)
    _FakeSMTP.instances.clear()
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    with pytest.raises(HTTPException) as e:
        m._send_email(db, "a@example.com, not-an-address", "Hi", "Hello")
    assert e.value.status_code == 400
    assert "not-an-address" in str(e.value.detail), e.value.detail
    assert not _FakeSMTP.instances, "nothing may send when an address is bad"


def test_single_address_still_sends(db, monkeypatch):
    _configure(db)
    _FakeSMTP.instances.clear()
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    out = m._send_email(db, "solo@example.com", "Hi", "Hello")
    assert out["to"] == ["solo@example.com"], out
