"""Weekly report schedule alongside daily.

The daily mail gains a frequency choice: daily as today, or weekly on a
chosen weekday (Friday default). Weekly sends once per week even across
restarts, never twice, and an invalid weekday is refused at save time.

Run: python -m pytest backend/tests/test_report_schedule.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import backend.main as m  # noqa: E402


class _FakeSMTP:
    instances = []

    def __init__(self, *_a, **_k):
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, *_a):
        pass

    def sendmail(self, *_a):
        return {}


def _configure(db, **over):
    m._set_setting(db, "email.report_enabled", "true")
    m._set_setting(db, "email.smtp_host", "smtp.example.com")
    m._set_setting(db, "email.username", "shop@example.com")
    m._set_setting(db, "email.password", "secret")
    m._set_setting(db, "email.report_to", "owner@example.com")
    m._set_setting(db, "email.report_hour", "0")
    for k, v in over.items():
        m._set_setting(db, k, v)
    db.commit()


def test_weekly_sends_on_matching_weekday(db, monkeypatch):
    today = m._ist_wall()
    _configure(
        db,
        **{"email.report_frequency": "weekly",
           "email.report_weekday": str(today.weekday())},
    )
    _FakeSMTP.instances.clear()
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    assert m._report_tick(db=db) == "sent"
    assert _FakeSMTP.instances, "weekly mail never sent on its weekday"


def test_weekly_skips_other_weekdays(db, monkeypatch):
    today = m._ist_wall()
    _configure(
        db,
        **{"email.report_frequency": "weekly",
           "email.report_weekday": str((today.weekday() + 1) % 7)},
    )
    _FakeSMTP.instances.clear()
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    assert m._report_tick(db=db) == "waiting"
    assert not _FakeSMTP.instances


def test_weekly_sends_only_once_per_week(db, monkeypatch):
    today = m._ist_wall()
    _configure(
        db,
        **{"email.report_frequency": "weekly",
           "email.report_weekday": str(today.weekday())},
    )
    _FakeSMTP.instances.clear()
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    assert m._report_tick(db=db) == "sent"
    assert m._report_tick(db=db) != "sent"
    assert len(_FakeSMTP.instances) == 1, "same week mailed twice"


def test_invalid_weekday_refused_at_save(db):
    from types import SimpleNamespace

    with pytest.raises(HTTPException) as e:
        m.update_email_settings(
            {"report_frequency": "weekly", "report_weekday": 9},
            SimpleNamespace(headers={}, client=None),
            db,
        )
    assert e.value.status_code == 400


def test_frequency_toggle_exists_in_settings():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "settings.html"), encoding="utf-8") as fh:
        html = fh.read()
    assert "report_frequency" in html or "Weekly" in html, (
        "no weekly schedule control on the Settings page"
    )
