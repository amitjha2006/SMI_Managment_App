"""Backup folder setting, IST timestamps, and login alerts.

Run: python -m pytest backend/tests/test_backup_login_alerts.py
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import backend.main as m  # noqa: E402
import backend.app.core.shared as shared  # noqa: E402
from backend.app.core.security import get_password_hash  # noqa: E402
from backend.app.models.user import User  # noqa: E402
from backend.app.routes.auth import login  # noqa: E402
from backend.app.routes.settings import update_backup_settings  # noqa: E402
from backend.app.core.shared import _set_setting  # noqa: E402


def _req(user_token=None):
    headers = {}
    if user_token:
        headers["Authorization"] = f"Bearer {user_token}"
    return SimpleNamespace(query_params={"reason": "test change note"}, headers=headers, client=None)


def _mkuser(db, username="alert_owner"):
    u = User(username=username, hashed_password=get_password_hash("secret123"),
             role="admin", is_active=True)
    db.add(u)
    db.commit()
    return u


def _mk_admin_token(db, username="admin"):
    from backend.app.core.security import create_access_token
    u = User(username=username, hashed_password=get_password_hash("secret123"),
             role="admin", is_active=True)
    db.add(u)
    db.commit()
    return create_access_token(data={"sub": username})


def _set_email(db):
    import json
    _set_setting(db, "email", json.dumps({
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_user": "app@example.com",
        "smtp_password": "pw",
        "from_email": "app@example.com",
        "from_name": "Shresth Masala",
        "use_tls": True,
        "alert_recipient": "owner@example.com",
    }))
    db.commit()


def test_login_succeeds_without_email_configured(db, monkeypatch):
    _mkuser(db)
    called = []
    monkeypatch.setattr(
        shared,
        "_send_email",
        lambda *a, **_k: called.append(a)
        or (_ for _ in ()).throw(AssertionError("no mail expected")),
    )
    out = login("alert_owner", "secret123", _req(), db)
    assert out["token_type"] == "bearer"
    assert not called


def test_login_sends_alert_when_configured(db, monkeypatch):
    _mkuser(db)
    _set_email(db)
    sent = []
    monkeypatch.setattr(
        shared,
        "_send_email",
        lambda _db, to, subject, body, _html=None: sent.append((to, subject, body)),
    )
    out = login("alert_owner", "secret123", _req(), db)
    assert out["token_type"] == "bearer"
    assert len(sent) == 1
    to, subject, body = sent[0]
    assert to == "owner@example.com"
    assert "alert_owner" in subject
    assert "IST" in body


def test_login_survives_smtp_failure(db, monkeypatch):
    _mkuser(db)
    _set_email(db)

    def _boom(*_a, **_k):
        raise OSError("SMTP down")

    monkeypatch.setattr(shared, "_send_email", _boom)
    out = login("alert_owner", "secret123", _req(), db)
    assert out["token_type"] == "bearer"


def test_backup_folder_rejects_relative_path(db):
    admin_token = _mk_admin_token(db)
    with pytest.raises(HTTPException) as e:
        update_backup_settings({"folder": "backups/pen"}, _req(admin_token), db)
    assert e.value.status_code == 400


def test_backup_folder_accepts_absolute_and_clears(db, tmp_path):
    admin_token = _mk_admin_token(db)
    target = str(tmp_path / "mybackups")
    out = update_backup_settings({"folder": target}, _req(admin_token), db)
    assert out["folder"] == target
    assert (tmp_path / "mybackups").is_dir()
    out = update_backup_settings({"folder": ""}, _req(admin_token), db)
    assert out["folder"] == ""


from backend.app.routes.backup import create_backup  # noqa: E402

def test_do_backup_uses_ist_and_custom_folder(db, tmp_path):
    admin_token = _mk_admin_token(db)
    # Use update_backup_settings to set the folder properly
    update_backup_settings({"folder": str(tmp_path)}, _req(admin_token), db)
    before = datetime.now(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)
    # create_backup requires a request object with admin auth; pass a minimal one
    req = _req(admin_token)
    rec = create_backup(request=req, db=db)
    after = datetime.now(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)
    assert (tmp_path / rec.filename).exists()
    assert before <= rec.backup_date <= after
    dumped = m.BackupRecordSchema.model_validate(rec).model_dump(mode="json")
    assert dumped["backup_date"].endswith("+05:30"), dumped["backup_date"]
