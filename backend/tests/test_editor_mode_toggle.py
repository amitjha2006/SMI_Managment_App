"""Editor Mode toggle must work on a live session.

Toggling keeps the original expiry: the new token carries the editor claim
with (approximately) the old token's remaining lifetime, never an error
for a session that is plainly still valid.

Run: python -m pytest backend/tests/test_editor_mode_toggle.py -v
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app.core.security import create_access_token, decode_token  # noqa: E402


def _mkuser(db, username):
    from backend.app.models.user import User

    u = User(username=username, hashed_password="x", role="admin", is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _req(token):
    return SimpleNamespace(
        headers={"Authorization": "Bearer " + token}, client=None)


def test_toggle_on_with_live_session(db):
    _mkuser(db, "toggle_admin")
    tok = create_access_token(data={"sub": "toggle_admin"})
    old_exp = decode_token(tok)["exp"]
    out = m.set_editor_mode({"enabled": True}, _req(tok), db)
    assert out["editor_mode"] is True
    claims = decode_token(out["access_token"])
    assert claims["editor"] is True
    assert abs(claims["exp"] - old_exp) < 120, (
        "toggling must preserve the original expiry, not extend or shrink it"
    )


def test_toggle_off_with_live_session(db):
    _mkuser(db, "toggle_admin2")
    tok = create_access_token(data={"sub": "toggle_admin2", "editor": True})
    out = m.set_editor_mode({"enabled": False}, _req(tok), db)
    assert out["editor_mode"] is False
    assert decode_token(out["access_token"]).get("editor") in (None, False)
