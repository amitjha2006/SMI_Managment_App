"""Session lock timeout setting.

Run: python -m pytest backend/tests/test_session_lock.py
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from backend.app.core.security import create_access_token
from backend.app.models.user import User, UserRole

import backend.main as m  # noqa: E402


def _req(db):
    u = db.query(User).filter(User.role == UserRole.ADMIN).first()
    if not u:
        u = User(username="lock_admin", hashed_password="x", role=UserRole.ADMIN, is_active=True)
        db.add(u)
        db.commit()
        db.refresh(u)
    token = create_access_token({"sub": u.username})
    return SimpleNamespace(
        query_params={"reason": "test change note"},
        headers={"Authorization": f"Bearer {token}"},
        client=None,
    )


def test_default_is_15_minutes(db):
    assert m.get_session_settings(db) == {"lock_minutes": 15}


def test_save_and_read_back(db):
    out = m.update_session_settings({"lock_minutes": 30}, _req(db), db)
    assert out == {"lock_minutes": 30}
    assert m.get_session_settings(db) == {"lock_minutes": 30}


def test_zero_disables(db):
    assert m.update_session_settings({"lock_minutes": 0}, _req(db), db) == {"lock_minutes": 0}


def test_out_of_range_refused(db):
    for bad in (-1, 241, "soon"):
        with pytest.raises(HTTPException) as e:
            m.update_session_settings({"lock_minutes": bad}, _req(db), db)
        assert e.value.status_code == 400
    # Refused writes change nothing.
    assert m.get_session_settings(db) == {"lock_minutes": 15}
