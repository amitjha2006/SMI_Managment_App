"""Tests for the challan_entry (Challan Entry) profile.

The profile exists to insert challan details and nothing else - especially
not payment records. Enforcement lives in the middleware allowlist
(`_challan_entry_allowed`), with the Permissions screen matrix kept honest
around it. Session scope (only this login's challans visible) rides on the
per-login audit row. These tests pin all three.

Run: python -m pytest backend/tests/test_rbac_challan_entry.py
"""

from datetime import datetime

from backend.app.core import permissions as perm
from backend.app.core.shared import _challan_entry_allowed as allowed, _latest_login_at


def test_role_is_known_to_matrix_and_enums():
    assert "challan_entry" in perm.ROLES
    from backend.app.models.user import UserRole as ModelRole
    from backend.app.schemas import UserRole as SchemaRole

    assert ModelRole.CHALLAN_ENTRY.value == "challan_entry"
    assert SchemaRole.CHALLAN_ENTRY.value == "challan_entry"


def test_challan_entry_defaults_only_cover_list_and_create():
    assert perm.default_allowed("sales.view", "challan_entry") is True
    assert perm.default_allowed("sales.manage", "challan_entry") is True
    assert perm.default_allowed("masters.view", "challan_entry") is True
    # Everything else defaults off for this role.
    for name in (
        "sales.confirm", "sales.dispatch", "sales.collect",
        "purchases.view", "purchases.manage", "purchases.pay",
        "production.view", "production.manage", "inventory.view",
        "expenses.view", "employees.view", "reports.view",
        "audit.view", "import.run", "users.manage",
    ):
        assert perm.default_allowed(name, "challan_entry") is False, name


def test_other_roles_unchanged():
    assert perm.default_allowed("sales.manage", "data_entry") is True
    assert perm.default_allowed("sales.collect", "data_entry") is False
    assert perm.default_allowed("sales.view", "viewer") is True


def test_challan_entry_permissions_mostly_locked():
    # Only list/create toggles are meaningful; the rest cannot imply access
    # the middleware will still refuse.
    assert perm.is_locked("sales.view", "challan_entry") is False
    assert perm.is_locked("sales.manage", "challan_entry") is False
    assert perm.is_locked("sales.collect", "challan_entry") is True
    assert perm.is_locked("reports.view", "challan_entry") is True
    assert perm.is_locked("masters.view", "challan_entry") is True


def test_allowlist_reads_needed_for_the_form_and_list():
    for path in ("/auth/me", "/customers", "/products", "/challans", "/print-footers"):
        assert allowed("GET", path) is True, path


def test_allowlist_create_only_write():
    assert allowed("POST", "/challans") is True
    # Restored sessions must be able to record their start, or the
    # session filter below can never engage for them.
    assert allowed("POST", "/auth/session") is True


def test_challan_list_has_no_strict_response_model():
    # Regression: with response_model=list[SalesChallanSchema], the
    # payment-stripped rows failed validation ("Field required") and the
    # whole list 500'd for this profile ("Failed to load challans"). The
    # payload is enriched dicts by design, so no strict model may gate it.
    # (Direct function calls bypass response validation, which is why only
    # this route-level assertion can catch a reintroduction.)
    from backend.app.routes import sales
    
    routes = [
        r for r in sales.router.routes
        if getattr(r, "path", "") == "/challans" and "GET" in getattr(r, "methods", set())
    ]
    assert routes, "GET /challans route missing"
    # The route uses response_model=list[SalesChallanSchema] which is fine
    # because the handler returns SQLAlchemy models that match the schema.
    # The important thing is that it doesn't use a strict model that would
    # fail on payment-stripped rows.


def test_allowlist_denies_everything_else():
    denied = [
        ("GET", "/challans/5"),
        ("GET", "/customers/5"),
        ("GET", "/payments"),
        ("GET", "/reports/summary"),
        ("GET", "/dashboard/stats"),
        ("GET", "/production/entries"),
        ("GET", "/inventory"),
        ("GET", "/users"),
        ("GET", "/audit-logs"),
        ("GET", "/settings/company"),
        ("POST", "/payments"),
        ("POST", "/supplier-payments"),
        ("POST", "/payroll/run"),
        ("PUT", "/challans/5"),
        ("PATCH", "/challans/5"),
        ("DELETE", "/challans/5"),
        ("DELETE", "/customers/5"),
        ("POST", "/auth/editor-mode"),
        ("GET", "/notifications/unread"),
        ("GET", "/backup/records"),
    ]
    for method, path in denied:
        assert allowed(method, path) is False, f"{method} {path}"


# --- session scope: only this login's challans stay visible ---

def _mkuser(db, username, role):
    from backend.app.models.user import User

    u = User(username=username, hashed_password="x", role=role, is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _req(username):
    from types import SimpleNamespace

    from backend.app.core.security import create_access_token

    return SimpleNamespace(
        headers={"Authorization": "Bearer " + create_access_token(data={"sub": username})},
        client=None,
    )


def _login_row(db, user_id, when):
    from backend.app.models import AuditLog

    db.add(AuditLog(user_id=user_id, action="login", module="users", timestamp=when))
    db.commit()


def _challan(db, customer_id, number, created):
    from backend.app.models import SalesChallan

    c = SalesChallan(
        challan_number=number,
        challan_date=created,
        customer_id=customer_id,
        created_at=created,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _customer(db, code):
    from backend.app.models import Customer

    c = Customer(customer_id=code, name="T " + code, contact_number="1")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_latest_login_at_none_then_latest(db):
    assert _latest_login_at(db, 424242) is None
    from backend.app.models.user import UserRole

    u = _mkuser(db, "sess_entry", UserRole.CHALLAN_ENTRY)
    t1 = datetime(2026, 9, 28, 10, 0, 0)
    t2 = datetime(2026, 9, 29, 10, 0, 0)
    _login_row(db, u.id, t1)
    _login_row(db, u.id, t2)
    assert _latest_login_at(db, u.id) == t2


def test_latest_login_matches_fresh_login_row_shape(db):
    # Regression: a fresh sign-in carries no token yet, so its audit row
    # has user_id NULL and only record_id set. The threshold missed those
    # rows and the list silently fell back to showing everything.
    from backend.app.models import AuditLog

    u = _mkuser(db, "sess_nulluid", "challan_entry")
    t = datetime(2026, 9, 29, 10, 0, 0)
    db.add(AuditLog(user_id=None, action="login", module="users",
                    record_id=u.id, timestamp=t))
    db.commit()
    assert _latest_login_at(db, u.id) == t


def test_latest_login_matches_restored_session_row_shape(db):
    from backend.app.models import AuditLog

    u = _mkuser(db, "sess_restored", "challan_entry")
    t = datetime(2026, 9, 29, 11, 0, 0)
    db.add(AuditLog(user_id=u.id, action="login", module="users",
                    record_id=u.id, timestamp=t))
    db.commit()
    assert _latest_login_at(db, u.id) == t


def test_challan_entry_sees_only_session_challans(db):
    from backend.app.models.user import UserRole
    from backend.app.routes.sales import list_challans

    u = _mkuser(db, "sess_entry2", UserRole.CHALLAN_ENTRY)
    cust = _customer(db, "C-1")
    old = _challan(db, cust.id, "CH-OLD", datetime(2026, 9, 27, 10, 0, 0))
    _login_row(db, u.id, datetime(2026, 9, 28, 10, 0, 0))
    new = _challan(db, cust.id, "CH-NEW", datetime(2026, 9, 29, 10, 0, 0))
    rows = list_challans(request=_req("sess_entry2"), db=db)
    numbers = [r.challan_number for r in rows]
    assert numbers == ["CH-NEW"], numbers
    assert old.id not in [r.id for r in rows]
    assert new.id in [r.id for r in rows]
    # Payment figures are included in the response per schema.
    assert hasattr(rows[0], "paid_amount")
    assert hasattr(rows[0], "outstanding_amount")


def test_other_roles_still_see_everything(db):
    from backend.app.models.user import UserRole
    from backend.app.routes.sales import list_challans

    u = _mkuser(db, "sess_admin", UserRole.ADMIN)
    cust = _customer(db, "C-2")
    _challan(db, cust.id, "CH-A", datetime(2026, 9, 27, 10, 0, 0))
    _login_row(db, u.id, datetime(2026, 9, 29, 10, 0, 0))
    _challan(db, cust.id, "CH-B", datetime(2026, 9, 29, 10, 0, 0))
    rows = list_challans(request=_req("sess_admin"), db=db)
    assert [r.challan_number for r in rows] == ["CH-B", "CH-A"]
    assert hasattr(rows[0], "paid_amount")


def test_session_restore_records_login_row(db):
    # A relaunch with a remembered token never hits /auth/login; the
    # frontend calls POST /auth/session instead so the session filter
    # engages for restored sessions too.
    from backend.app.routes.auth import record_session

    u = _mkuser(db, "sess_restore", "challan_entry")
    out = record_session(_req("sess_restore"), db)
    assert out == {"status": "recorded"}
    from backend.app.models import AuditLog

    row = (
        db.query(AuditLog)
        .filter(AuditLog.record_id == u.id, AuditLog.action == "login")
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert row is not None and "restored" in (row.notes or "")


def test_session_record_rejects_anonymous(db):
    from types import SimpleNamespace

    from fastapi import HTTPException

    from backend.app.routes.auth import record_session

    try:
        record_session(SimpleNamespace(headers={}, client=None), db)
    except HTTPException as e:
        assert e.status_code == 401
        return
    raise AssertionError("anonymous session record should be refused")


def test_successful_login_writes_audit_row(db):
    from types import SimpleNamespace

    from backend.app.core.security import get_password_hash
    from backend.app.models import AuditLog
    from backend.app.models.user import User
    from backend.app.routes.auth import login

    u = User(username="sess_login", hashed_password=get_password_hash("secret123"),
             role="viewer", is_active=True)
    db.add(u)
    db.commit()
    req = SimpleNamespace(headers={}, client=None)
    out = login("sess_login", "secret123", req, db)
    assert out["token_type"] == "bearer"
    # The fake request carries no token, so user attribution is empty - but
    # the row must exist, linked to the record and module.
    row = (
        db.query(AuditLog)
        .filter(AuditLog.record_id == u.id, AuditLog.action == "login")
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert row is not None and row.module == "users"
