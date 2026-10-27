"""User management, audit logs, permissions routes"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.security import get_password_hash
from backend.app.models import (
    AuditLog,
    BackupRecord,
    Customer,
    CustomerPayment,
    Employee,
    Expense,
    FinishedProductStockMovement,
    PackagingSize,
    Product,
    ProductCategory,
    RawMaterial,
    RawMaterialCategory,
    RolePermission,
    SalesChallan,
    SalesChallanItem,
    SalesInvoice,
    SavedReport,
    Supplier,
    SupplierPayment,
    PurchaseInvoice,
    ProductionEntry,
    User,
    UserRole,
)
from backend.app.schemas import AuditLogSchema, UserCreate, UserSchema, UserUpdate
from backend.app.core.shared import (
    _audit,
    _MIN_PASSWORD_LEN,
    _request_payload,
    _request_user_and_role,
    _iso_ist,
    _require_admin,
)

router = APIRouter()


@router.post("/users", response_model=UserSchema)
def create_user(
    user: UserCreate, request: Request, db: Session = Depends(get_db)
):
    """Create a staff account (admin only, via the admin_only gate in rbac_guard).

    This route did not exist. The "Add User" form in the frontend posted to
    /auth/register, which is the first-run bootstrap and is only reachable
    while the users table is empty - so the button could never add a second
    person. An installation could therefore only ever have the single account
    its owner registered, with no way to give a manager, data entry clerk or
    challan entry operator their own login.

    Editor Mode is required here even though the middleware only gates
    PUT/PATCH/DELETE by method. Changing an existing user's role is already
    editor-gated by that rule, and minting a brand new admin is at least as
    sensitive - it must not be the easy way round.
    """
    payload = _request_payload(request)
    if not (payload and payload.get("editor")):
        raise HTTPException(
            status_code=403,
            detail="Editor Mode is off. Switch Editor Mode on to add a user.",
        )

    if db.query(User).filter(User.username == user.username).first():
        raise HTTPException(status_code=400, detail="Username already taken")
    if user.email and db.query(User).filter(User.email == user.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    created = User(
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        hashed_password=get_password_hash(user.password),
        role=user.role,
        is_active=True,
    )
    db.add(created)
    _audit(
        db,
        request,
        "users",
        "create",
        record=created,
        record_type="user",
        new_values={
            "username": created.username,
            "role": str(getattr(created.role, "value", created.role) or ""),
        },
        notes="Staff account created by administrator",
    )
    db.commit()
    db.refresh(created)
    return created


@router.get("/users", response_model=list[UserSchema])
def list_users(db: Session = Depends(get_db)):
    return db.query(User).order_by(User.username).all()


@router.put("/users/{user_id}", response_model=UserSchema)
def update_user(user_id: int, update: UserUpdate, request: Request, db: Session = Depends(get_db)):

    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    _old_values = {
        "username": u.username,
        "email": u.email,
        "role": str(getattr(u.role, "value", u.role) or ""),
        "is_active": bool(u.is_active),
        "full_name": u.full_name,
    }
    data = update.dict(exclude_unset=True)
    if data.get("username"):
        exists = (
            db.query(User).filter(User.username == data["username"], User.id != user_id).first()
        )
        if exists:
            raise HTTPException(status_code=400, detail="Username already registered. Sign in instead, or pick a different username.")
    if data.get("email"):
        exists = db.query(User).filter(User.email == data["email"], User.id != user_id).first()
        if exists:
            raise HTTPException(status_code=400, detail="Email already registered. Sign in instead, or use a different email.")
    for field, value in data.items():
        if value is not None:
            setattr(u, field, value)
    _audit(
        db,
        request,
        "users",
        "update",
        record=u,
        record_type="user",
        old_values=_old_values,
        new_values={
            "username": u.username,
            "email": u.email,
            "role": str(getattr(u.role, "value", u.role) or ""),
            "is_active": bool(u.is_active),
            "full_name": u.full_name,
        },
        notes=", ".join(sorted(data.keys())) or None,
    )
    db.commit()
    db.refresh(u)
    return u


@router.delete("/users/{user_id}")
def deactivate_user(user_id: int, request: Request, db: Session = Depends(get_db)):

    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    u.is_active = False
    _audit(
        db,
        request,
        "users",
        "deactivate",
        record=u,
        record_type="user",
        old_values={"is_active": True},
        new_values={"is_active": False, "username": u.username},
    )
    db.commit()
    return {"status": "deactivated", "id": user_id}


@router.put("/users/{user_id}/password")
def set_user_password(user_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):

    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    password = payload.get("password") or ""
    if len(password) < _MIN_PASSWORD_LEN:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    u.hashed_password = get_password_hash(password)
    # Never record the password itself, only that it changed.
    _audit(
        db,
        request,
        "users",
        "password_change",
        record=u,
        record_type="user",
        new_values={"username": u.username, "password_changed": True},
    )
    db.commit()
    return {"status": "password updated", "id": user_id}


# What an audit row points at, and what to call it. record_type is written by
# each _audit() call site as either a singular name ("sales_challan") or the
# table name ("expenses", "production_entries"), so both spellings are listed
# rather than normalised - stripping a trailing "s" would miss "expenses".
# Value: (model, field name or formatter). The label is what a human would say
# out loud (a document number, a name), so nobody has to open "View" to find
# out which challan or invoice a row is about.
_AUDIT_LABELS = {
    "user": (User, "username"),
    "customer": (Customer, "name"),
    "supplier": (Supplier, "name"),
    "raw_material": (RawMaterial, "name"),
    "product": (Product, "name"),
    "products": (Product, "name"),
    "product_category": (ProductCategory, "name"),
    "raw_material_category": (RawMaterialCategory, "name"),
    "employee": (Employee, "name"),
    "backup_record": (BackupRecord, "filename"),
    "saved_report": (SavedReport, "name"),
    "sales_invoice": (SalesInvoice, lambda i: f"Invoice {i.invoice_number}"),
    "purchase_invoice": (PurchaseInvoice, lambda i: f"Invoice {i.invoice_number}"),
    "purchase_invoices": (PurchaseInvoice, lambda i: f"Invoice {i.invoice_number}"),
    "production_entry": (ProductionEntry, lambda e: f"Production {e.production_number}"),
    "production_entries": (ProductionEntry, lambda e: f"Production {e.production_number}"),
    # A challan's number is a bare "277" - meaningless on its own, so the party
    # rides along and the label reads like the document itself.
    "sales_challan": (
        SalesChallan,
        lambda c: f"Challan {c.challan_number}"
        + (f" · {c.customer.name}" if c.customer else ""),
    ),
    "expenses": (Expense, lambda e: f"{e.description} · ₹{e.amount}"),
    "expense": (Expense, lambda e: f"{e.description} · ₹{e.amount}"),
    # Rows with no number of their own: name the document they belong to.
    "packaging_size": (
        PackagingSize,
        lambda s: f"{s.product.name} · {s.size_name}" if s.product else s.size_name,
    ),
    "sales_challan_item": (
        SalesChallanItem,
        lambda i: f"Challan {i.challan.challan_number}" if i.challan else None,
    ),
    "customer_payment": (
        CustomerPayment,
        lambda p: (
            f"Challan {p.challan.challan_number} · ₹{p.amount}" if p.challan else None
        ),
    ),
    "supplier_payment": (
        SupplierPayment,
        lambda p: (
            f"Invoice {p.purchase_invoice.invoice_number} · ₹{p.amount}"
            if p.purchase_invoice
            else None
        ),
    ),
    "finished_product_stock_movement": (
        FinishedProductStockMovement,
        lambda m: m.product.name if m.product else None,
    ),
}


def _audit_record_labels(db, rows) -> dict[tuple[str, int], str]:
    """(record_type, record_id) -> "CH-2026-0042". One query per type for the
    whole page, never one per row. A record deleted since simply gets no
    label, and the page falls back to the raw "type #id". A corrupt row or
    missing relationship never breaks the whole page."""
    wanted: dict[str, set[int]] = {}
    for r in rows:
        if r.record_id and r.record_type in _AUDIT_LABELS:
            wanted.setdefault(r.record_type, set()).add(r.record_id)
    labels: dict[tuple[str, int], str] = {}
    for rtype, ids in wanted.items():
        try:
            model, spec = _AUDIT_LABELS[rtype]
            for rec in db.query(model).filter(model.id.in_(ids)).all():
                try:
                    label = spec(rec) if callable(spec) else getattr(rec, spec, None)
                    if label:
                        labels[(rtype, rec.id)] = str(label)
                except Exception:
                    pass  # one bad record shouldn't break the whole page
        except Exception:
            pass  # one bad record type shouldn't break the whole page
    return labels


@router.get("/audit-logs", response_model=list[AuditLogSchema])
def list_audit_logs(
    module: str | None = None,
    action: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    """Spec 3: audit history showing user, time, module, record, action, before/after."""
    q = db.query(AuditLog).order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
    if module:
        q = q.filter(AuditLog.module == module)
    if action:
        q = q.filter(AuditLog.action == action)
    rows = q.limit(min(max(limit, 1), 1000)).all()
    users = {u.id: u.username for u in db.query(User).all()}
    labels = _audit_record_labels(db, rows)
    return [
        {
            "id": r.id,
            "user_id": r.user_id,
            "user_username": users.get(r.user_id) or "system",
            "action": r.action,
            "module": r.module,
            "record_id": r.record_id,
            "record_type": r.record_type,
            "record_label": labels.get((r.record_type, r.record_id)),
            "old_values": r.old_values,
            "new_values": r.new_values,
            "timestamp": _iso_ist(r.timestamp),
            "ip_address": r.ip_address,
            "notes": r.notes,
        }
        for r in rows
    ]


@router.get("/permissions")
def list_permissions(request: Request, db: Session = Depends(get_db)):
    """Effective permission matrix (admin). Shows default vs overridden."""
    _require_admin(request, db)
    from backend.app.core import permissions as _perm

    overrides = {r: _perm.load_overrides(db, r) for r in _perm.ROLES}
    rows = []
    for name, module, action, description, _defaults in _perm.PERMISSIONS:
        entry = {
            "name": name,
            "module": module,
            "action": action,
            "description": description,
            "roles": {},
        }
        for role in _perm.ROLES:
            default = _perm.default_allowed(name, role)
            explicit = overrides.get(role, {}).get(name)
            entry["roles"][role] = {
                "default": default,
                "granted": _perm.effective(name, role, overrides.get(role, {})),
                "override": explicit,
                "locked": _perm.is_locked(name, role),
            }
        rows.append(entry)
    return {"roles": _perm.ROLES, "permissions": rows}


@router.put("/permissions")
def update_permissions(payload: dict, request: Request, db: Session = Depends(get_db)):

    """Save explicit Admin choices for one role (admin only)."""
    _require_admin(request, db)
    from backend.app.core import permissions as _perm
    from backend.app.models.user import RolePermission

    role = str((payload or {}).get("role") or "").strip()
    grants = (payload or {}).get("grants")
    if role not in _perm.ROLES:
        raise HTTPException(status_code=400, detail=f"Unknown role: {role}")
    if not isinstance(grants, dict):
        raise HTTPException(status_code=400, detail="grants must be an object")

    valid = {p[0] for p in _perm.PERMISSIONS}
    unknown = [k for k in grants if k not in valid]
    if unknown:
        raise HTTPException(
            status_code=400, detail=f"Unknown permission(s): {', '.join(unknown[:5])}"
        )

    if role == "admin":
        raise HTTPException(
            status_code=400, detail="Admin permissions are fixed and cannot be changed"
        )

    user, _role = _request_user_and_role(db, request)
    existing = {
        r.permission: r for r in db.query(RolePermission).filter(RolePermission.role == role).all()
    }

    changed = []
    for name, value in grants.items():
        if _perm.is_locked(name, role):
            continue  # locked floor for this role - ignore attempts to drop it
        want = bool(value)
        row = existing.get(name)
        if want == _perm.default_allowed(name, role):
            # back to default: drop the override so it tracks the default again
            if row is not None:
                db.delete(row)
                changed.append({"permission": name, "granted": None})
            continue
        if row is None:
            row = RolePermission(role=role, permission=name, granted=want)
            db.add(row)
        else:
            row.granted = want
        row.updated_by = user.id if user else None
        changed.append({"permission": name, "granted": want})

    _audit(
        db,
        request,
        "permissions",
        "update",
        record_type="role_permissions",
        old_values=None,
        new_values={"role": role, "changes": {c["permission"]: c["granted"] for c in changed}},
    )
    db.commit()
    return {"status": "saved", "role": role, "changed": len(changed)}