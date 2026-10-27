"""Admin-created staff accounts.

The Users screen has always had an "Add User" form, and it posted to
/auth/register - the first-run bootstrap, which the middleware only lets
through while the users table is empty. So on any real installation the
button could not add anybody: the one account that exists is the owner's, and
there was no route to create a second one. A factory with a manager, a data
entry clerk and a challan entry operator had no way to give them logins.

These tests pin the replacement route: admin-only, Editor Mode gated, and
audited.

Run: python -m pytest backend/tests/test_user_creation.py
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.app.core.security import create_access_token

ROOT = r"C:\Shresth_Masala_Application"


def _mkuser(db, username, role="admin"):
    from backend.app.models.user import User

    u = User(username=username, hashed_password="x", role=role, is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _req(username, editor=True):
    return SimpleNamespace(
        headers={
            "Authorization": "Bearer "
            + create_access_token(data={"sub": username, "editor": editor})
        },
        client=None,
    )


def _payload(username, role="data_entry"):
    from backend.app.schemas import UserCreate

    return UserCreate(
        username=username,
        password="secret123",
        full_name="Staff One",
        email=f"{username}@example.com",
        role=role,
    )


def test_admin_can_create_a_staff_account(db):
    from backend.main import create_user

    _mkuser(db, "owner_admin")
    created = create_user(_payload("new_clerk", "data_entry"), _req("owner_admin"), db)
    assert created.username == "new_clerk"
    assert str(created.role.value) == "data_entry"
    assert created.is_active is True
    assert created.hashed_password != "secret123", "password was not hashed"


def test_created_user_can_immediately_sign_in(db):
    """The point of the route: a usable login, not just a row."""
    from backend.app.core.security import verify_password
    from backend.main import create_user

    _mkuser(db, "owner_admin2")
    created = create_user(_payload("usable_login"), _req("owner_admin2"), db)
    assert verify_password("secret123", created.hashed_password)


def test_editor_mode_is_required(db):
    """Creating an admin must not be easier than promoting one.

    PUT /users/{id} (a role change) is editor-gated by the middleware's
    method rule. POST is not, so without this check a new admin account would
    slip through the one door the gate does not watch.
    """
    from backend.main import create_user

    _mkuser(db, "owner_admin3")
    with pytest.raises(HTTPException) as e:
        create_user(_payload("sneaky_admin", "admin"), _req("owner_admin3", editor=False), db)
    assert e.value.status_code == 403
    assert "Editor Mode" in str(e.value.detail)


def test_duplicate_username_rejected(db):
    from backend.main import create_user

    _mkuser(db, "owner_admin4")
    create_user(_payload("clash"), _req("owner_admin4"), db)
    with pytest.raises(HTTPException) as e:
        create_user(_payload("clash"), _req("owner_admin4"), db)
    assert e.value.status_code == 400


def test_duplicate_email_rejected(db):
    from backend.main import create_user

    _mkuser(db, "owner_admin5")
    create_user(_payload("first_email"), _req("owner_admin5"), db)
    from backend.app.schemas import UserCreate

    second = UserCreate(
        username="different_name",
        password="secret123",
        email="first_email@example.com",
        role="viewer",
    )
    with pytest.raises(HTTPException) as e:
        create_user(second, _req("owner_admin5"), db)
    assert e.value.status_code == 400


def test_creation_is_audited(db):
    from backend.app.models import AuditLog
    from backend.main import create_user

    owner = _mkuser(db, "owner_admin6")
    created = create_user(_payload("audited_user"), _req("owner_admin6"), db)
    row = (
        db.query(AuditLog)
        .filter(AuditLog.action == "create", AuditLog.module == "users")
        .first()
    )
    assert row is not None, "creating a user was not audited"
    assert row.user_id == owner.id
    assert row.record_id == created.id


def test_frontend_posts_to_users_not_the_bootstrap():
    """Guards the original defect: the form pointed at /auth/register, which
    is closed to everyone once the first account exists."""
    with open(
        ROOT + "\\frontend\\pages\\users.html", encoding="utf-8"
    ) as fh:
        src = fh.read()
    body = src.split("window.saveUser")[1]
    assert 'fetch("/users", { method: "POST"' in body, (
        "the Add User form must POST to /users; /auth/register is the "
        "first-run bootstrap and only works on an empty database"
    )
    assert "/auth/register" not in body


def test_route_is_admin_only_via_the_existing_middleware():
    """Authorization is not re-implemented per route; /users is already in the
    admin_only list, so a new route under that prefix inherits the gate."""
    import re

    with open(ROOT + "\\backend\\main.py", encoding="utf-8") as fh:
        src = fh.read()
    # Match the tuple body up to its closing paren. Splitting on ")" would cut
    # at the first one inside path.startswith("/users").
    m = re.search(r"admin_only\s*=\s*\((.*?)\n\s*\)", src, re.S)
    assert m, "admin_only block not found in rbac_guard"
    assert 'path.startswith("/users")' in m.group(1)
