"""Cancelling the dashboard Share prompt must not leave a live public link.

Reported bug: clicking Share on the dashboard published a public snapshot URL,
and dismissing the copy prompt still left that URL working. The link was
committed by POST /dashboard/share before anyone had agreed to share anything,
so a cancel could not be honoured - the only trace was a live row in Settings.

The fix has two halves, and both are needed:

1. The frontend revokes the just-created link when the prompt returns null.
   That DELETE used to be unreachable for the person who created the link: the
   Share button publishes with a plain POST, which Editor Mode does not gate,
   while revoke is a DELETE, which it did. So a manager who never switched
   Editor Mode on was refused the cleanup with "Editor Mode is off" and the
   orphan survived. /dashboard/shares is now exempt, making create and revoke
   symmetric.
2. Revoke is no longer admin-only. A manager may revoke their own link (the
   cancel case) but still not somebody else's; admins may revoke anything.

Run: python -m pytest backend/tests/test_dashboard_share_cancel.py
"""

import re
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.app.core.security import create_access_token
from backend.app.core.shared import _editor_mode_always_allowed
from backend.app.routes.dashboard import revoke_dashboard_share

ROOT = r"C:\Shresth_Masala_Application"


def _read(rel):
    with open(ROOT + "\\" + rel.replace("/", "\\"), encoding="utf-8") as fh:
        return fh.read()


def _req(username):
    return SimpleNamespace(
        headers={"Authorization": "Bearer " + create_access_token(data={"sub": username})},
        client=None,
    )


def _mkuser(db, username, role):
    from backend.app.models.user import User

    u = User(username=username, hashed_password="x", role=role, is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _share(db, link_id, created_by):
    from backend.app.models import DashboardShare

    s = DashboardShare(link_id=link_id, payload="{}", created_by=created_by)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


# --------------------------------------------------------------------------
# Editor Mode: the cleanup DELETE must not be refused
# --------------------------------------------------------------------------


def test_revoke_link_exempt_from_editor_mode():
    # The regression itself: with Editor Mode off this path used to 403, which
    # is how a cancelled share ended up permanently live.
    assert _editor_mode_always_allowed("/dashboard/shares/abc123def456"), (
        "/dashboard/shares/<link_id> must be exempt from Editor Mode, otherwise "
        "cancelling the copy prompt cannot revoke the link it just created"
    )
    assert _editor_mode_always_allowed("/dashboard/shares") is True


def test_share_exemption_does_not_leak_to_records():
    for p in ("/challans/1", "/sales/invoices/3", "/customers/1", "/settings/backup"):
        assert not _editor_mode_always_allowed(p), (
            f"{p} must still require Editor Mode; the share exemption leaked"
        )
    # Prefix matching must not turn a lookalike path into an exemption.
    assert not _editor_mode_always_allowed("/dashboard/sharesx")
    assert not _editor_mode_always_allowed("/dashboard/shared")


# --------------------------------------------------------------------------
# Revoke permissions: own link for a manager, any link for an admin
# --------------------------------------------------------------------------


def test_manager_can_revoke_own_link(db):
    u = _mkuser(db, "mgr_self", "manager")
    s = _share(db, "ownlink123", u.id)
    out = revoke_dashboard_share("ownlink123", _req("mgr_self"), db)
    assert out["status"] == "revoked"
    assert db.get(type(s), s.id) is None


def test_manager_cannot_revoke_someone_elses_link(db):
    _mkuser(db, "mgr_other", "manager")
    other = _mkuser(db, "mgr_owner", "manager")
    _share(db, "notmine123", other.id)
    with pytest.raises(HTTPException) as e:
        revoke_dashboard_share("notmine123", _req("mgr_other"), db)
    assert e.value.status_code == 403


def test_admin_can_revoke_any_link(db):
    _mkuser(db, "adm_root", "admin")
    other = _mkuser(db, "mgr_owner2", "manager")
    _share(db, "adminrev123", other.id)
    out = revoke_dashboard_share("adminrev123", _req("adm_root"), db)
    assert out["status"] == "revoked"


def test_revoke_requires_authentication(db):
    _mkuser(db, "mgr_anon", "manager")
    anon = SimpleNamespace(headers={}, client=None)
    with pytest.raises(HTTPException) as e:
        revoke_dashboard_share("whatever1", anon, db)
    assert e.value.status_code == 401


def test_revoke_unknown_link_is_404(db):
    _mkuser(db, "mgr_404", "manager")
    with pytest.raises(HTTPException) as e:
        revoke_dashboard_share("doesnotexist", _req("mgr_404"), db)
    assert e.value.status_code == 404


# --------------------------------------------------------------------------
# Frontend: cancel must revoke
# --------------------------------------------------------------------------


def test_share_prompt_cancel_revokes_the_link():
    src = _read("frontend/pages/dashboard.html")
    fn = re.search(
        r"window\.shareDashboard\s*=\s*async function[\s\S]*?\n\};", src
    )
    assert fn, "shareDashboard() not found in frontend/pages/dashboard.html"
    body = fn.group(0)
    assert "prompt(" in body, "the share prompt is gone; nothing to cancel"
    # A cancel is prompt() returning null. Falsy-checks (if (!copied)) would
    # also fire when the user clears the box and clicks OK, which should keep
    # the link.
    assert "=== null" in body, (
        "shareDashboard must test the prompt result against null, or it will "
        "revoke a link the user actually copied after clearing the field"
    )
    assert 'method: "DELETE"' in body, "cancelling must revoke the created link"
    assert "/dashboard/shares/" in body


def test_share_still_keeps_link_when_confirmed():
    src = _read("frontend/pages/dashboard.html")
    fn = re.search(r"window\.shareDashboard\s*=\s*async function[\s\S]*?\n\};", src)
    body = fn.group(0)
    # The success notice must sit after the cancel early-return, otherwise
    # cancelling reports success too.
    assert body.index("Share link discarded") < body.index("Share link created")
