"""Shared utilities for routes — extracted from main.py to avoid circular imports."""
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.core.database import SessionLocal, _ist_wall, get_db
from backend.app.core.security import decode_token
from backend.app.models import (
    ApplicationSetting,
    AuditLog,
    BackupRecord,
    Customer,
    CustomerPayment,
    DashboardPreference,
    DashboardShare,
    Employee,
    Expense,
    FinishedProductStockMovement,
    ImportReport,
    NotificationLog,
    PackagingSize,
    PrintFooter,
    Product,
    ProductCategory,
    PurchaseInvoice,
    PurchaseInvoiceItem,
    RawMaterial,
    RawMaterialStockMovement,
    SalaryPayment,
    SalesChallan,
    SalesChallanItem,
    Supplier,
    SupplierPayment,
    User,
)
from backend.app.models.finished_product import FinishedProductMovementType
from backend.app.models.user import UserRole


# Validation bounds
_MIN_PASSWORD_LEN = 6
_AGEING_RECENT_DAYS = 30
_AGEING_OVERDUE_DAYS = 60
_MAX_GST_PERCENT = 100
_FULL_PERCENT_SHARE = 100
_NEGATIVE_BAL_TOLERANCE_KG = 0.0005
_YEAR_STR_LEN = 4
_MONTHS_PER_YEAR = 12
_MAX_INVOICE_PREFIX_LEN = 8
_MAX_DOC_NUMBER_LEN = 50
_MAX_HOUR = 23
_MAX_WEEKDAY = 6
_MAX_BACKUP_KEEP = 90
_MAX_LOCK_MINUTES = 240
_MAX_PORT = 65535
_MAX_NOTIFICATIONS_KEPT = 100
_FY_START_MONTH = 4
_MIN_FY_YEAR = 2000
_MAX_FY_YEAR = 2100


# Audit retention
_MAX_AUDIT_KEPT = 5000


def _prune_audit_log(db: Session, keep: int) -> None:
    """Drop audit rows beyond the newest `keep`."""
    ids = [
        r[0]
        for r in db.query(AuditLog.id).order_by(AuditLog.id.desc()).limit(keep + 1).all()
    ]
    if len(ids) > keep:
        db.query(AuditLog).filter(AuditLog.id.in_(ids[keep:])).delete(
            synchronize_session=False
        )


def _audit(
    db: Session,
    request,
    module: str,
    action: str,
    record=None,
    record_id: Optional[int] = None,
    record_type: Optional[str] = None,
    old_values=None,
    new_values=None,
    notes: Optional[str] = None,
) -> None:
    """Write audit log entry (swallowed on failure)."""
    try:
        if record is not None:
            db.flush()
            record_id = record.id
            if record_type is None:
                record_type = record.__tablename__
        user, _role = _request_user_and_role(db, request)
        try:
            _qp = getattr(request, "query_params", None)
            q_reason = (_qp.get("reason") or "").strip() if _qp is not None else ""
        except Exception:
            q_reason = ""
        if q_reason:
            notes = (notes + ". " if notes else "") + f"Reason: {q_reason}"
        db.add(
            AuditLog(
                user_id=user.id if user else None,
                action=action,
                module=module,
                record_id=record_id,
                record_type=record_type,
                old_values=_json_dumps(old_values) if old_values else None,
                new_values=_json_dumps(new_values) if new_values else None,
                ip_address=request.client.host if request.client else None,
                notes=notes,
            )
        )
        db.flush()
        _prune_audit_log(db, _MAX_AUDIT_KEPT)
    except Exception:
        pass


def _json_dumps(obj):
    import json
    return json.dumps(obj, default=str)


def _archive_record(db: Session, request, record, module: str, record_type: Optional[str] = None) -> bool:
    """Soft delete a record."""
    if getattr(record, "is_archived", False):
        return False
    user = _current_user(db, request)
    record.is_archived = True
    record.archived_at = _ist_wall()
    record.archived_by = user.id if user else None
    _audit(
        db,
        request,
        module,
        "archive",
        record=record,
        record_type=record_type or record.__tablename__,
        old_values={"is_archived": False},
        new_values={"is_archived": True, "archived_by": user.username if user else None},
        notes="Archived.",
    )
    return True


_CHANGE_NOTE_MIN = 5
_C_note_hint = (
    "Please explain why this record is being changed (at least "
    f"{_CHANGE_NOTE_MIN} characters). The note is saved in the audit history."
)


def _change_reason(request, payload=None) -> str:
    """Mandatory change note for every edit / archive / restore / status move."""
    raw = ""
    if isinstance(payload, dict):
        raw = payload.get("reason") or ""
    if not raw:
        params = getattr(request, "query_params", None)
        if params is not None:
            raw = params.get("reason") or ""
    raw = (raw or "").strip()
    if len(raw) < _CHANGE_NOTE_MIN:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=_C_note_hint)
    return raw


def _reject_if_archived(record, label: str) -> None:
    """Block any write to an archived row."""
    if getattr(record, "is_archived", False):
        from fastapi import HTTPException
        raise HTTPException(
            status_code=409,
            detail=f"This {label} is archived. Restore it before making changes.",
        )


def _restore_record(db: Session, request, record, module: str, record_type: Optional[str] = None) -> bool:
    """Un-archive a soft-deleted row."""
    if not getattr(record, "is_archived", False):
        return False
    user = _current_user(db, request)
    record.is_archived = False
    record.archived_at = None
    record.archived_by = None
    _audit(
        db,
        request,
        module,
        "restore",
        record=record,
        record_type=record_type or record.__tablename__,
        old_values={"is_archived": True},
        new_values={"is_archived": False, "restored_by": user.username if user else None},
        notes="Restored.",
    )
    return True


def _request_user_and_role(db: Session, request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None, None
    payload = decode_token(auth[len("Bearer ") :].strip())
    if not payload or not payload.get("sub"):
        return None, None
    user = db.query(User).filter(User.username == payload["sub"]).first()
    if not user or not user.is_active:
        return None, None
    role = getattr(user.role, "value", user.role)
    return user, str(role)


def _request_payload(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return decode_token(auth[len("Bearer ") :].strip())


def _current_user(db: Session, request):
    user, _role = _request_user_and_role(db, request)
    return user


def _require_admin(request, db: Session):
    user, role = _request_user_and_role(db, request)
    if not user or role != "admin":
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def _challan_entry_allowed(method: str, path: str) -> bool:
    return method == "GET" and path in ("/challans", "/auth/me", "/customers", "/products", "/print-footers") or method == "POST" and path in ("/challans", "/auth/session")


def _latest_login_at(db: Session, user_id: int):
    """Get the latest login timestamp for a user from audit logs."""
    from backend.app.models import AuditLog
    # Check for login rows with user_id
    row = db.query(AuditLog).filter(
        AuditLog.action == "login",
        AuditLog.user_id == user_id,
        AuditLog.module == "users"
    ).order_by(AuditLog.timestamp.desc()).first()
    if row:
        return row.timestamp
    # Also check for login rows with record_id (legacy format)
    row = db.query(AuditLog).filter(
        AuditLog.action == "login",
        AuditLog.record_id == user_id,
        AuditLog.module == "users"
    ).order_by(AuditLog.timestamp.desc()).first()
    return row.timestamp if row else None


def _iso_ist(dt):
    if dt is None:
        return None
    try:
        naive = dt.replace(tzinfo=None) if getattr(dt, "tzinfo", None) else dt
    except Exception:
        naive = dt
    return naive.isoformat()


def _low_finished_sizes(db: Session) -> list:
    """Finished sizes at/below their per-size packet threshold.

    Returns a list of {size_name, packets, threshold_packets, product_name}
    for active packaging sizes whose on-hand balance (produced minus sold,
    expressed in packets via each size's weight_in_kg) is at or below
    `threshold_packets`. A size with zero balance qualifies (0 <= threshold),
    so an empty size with a zero threshold still alerts.

    Balance is computed per packaging_size_id from stock movements:
    PRODUCTION/OPENING add kg, SALES/MANUAL_ADJUSTMENT/WASTAGE/RETURN subtract kg.
    Retired (inactive) sizes never alert.
    """
    add_types = (
        FinishedProductMovementType.PRODUCTION,
        FinishedProductMovementType.OPENING,
    )
    sub_types = (
        FinishedProductMovementType.SALES,
        FinishedProductMovementType.ADJUSTMENT,
        FinishedProductMovementType.WASTAGE,
        FinishedProductMovementType.RETURN,
    )

    balance_by_size = {}
    for m in db.query(FinishedProductStockMovement).all():
        q = m.quantity or Decimal("0")
        key = m.packaging_size_id
        if m.movement_type in add_types:
            balance_by_size[key] = balance_by_size.get(key, Decimal("0")) + q
        elif m.movement_type in sub_types:
            balance_by_size[key] = balance_by_size.get(key, Decimal("0")) - q

    lows = []
    for size in (
        db.query(PackagingSize)
        .filter(PackagingSize.is_active == True)  # noqa: E712
        .all()
    ):
        weight = size.weight_in_kg or Decimal("0")
        balance_kg = balance_by_size.get(size.id, Decimal("0"))
        packets = (balance_kg / weight) if weight else Decimal("0")
        threshold = size.threshold_packets or Decimal("0")
        if packets <= threshold:
            product = size.product
            lows.append({
                "size_name": size.size_name,
                "packets": float(packets),
                "threshold_packets": float(threshold),
                "product_name": product.name if product else None,
            })
    return lows


def _send_login_alert(db: Session, request, username: str) -> None:
    """Send a sign-in alert email using the generic _send_email helper.
    
    Reads email config from the 'email' ApplicationSetting and sends to
    alert_recipient if configured. Best-effort: never blocks login.
    """
    import json
    from backend.app.models import ApplicationSetting
    settings_row = db.query(ApplicationSetting).filter(ApplicationSetting.key == "email").first()
    if not settings_row or not settings_row.value:
        return
    try:
        cfg = json.loads(settings_row.value) if isinstance(settings_row.value, str) else settings_row.value
    except Exception:
        return
    if not cfg.get("alert_recipient"):
        return
    _send_email(
        db,
        cfg["alert_recipient"],
        f"Shresth Masala - Sign-in Alert: {username}",
        f"Sign-in detected for user: {username}\nTime: {_ist_wall().strftime('%Y-%m-%d %H:%M:%S')} IST",
    )


def _send_email(db: Session, to_email: str, subject: str, body: str) -> bool:
    """Generic email sender using configured SMTP settings."""
    from backend.app.models import ApplicationSetting
    settings_row = db.query(ApplicationSetting).filter(ApplicationSetting.key == "email").first()
    if not settings_row:
        return False
    cfg = settings_row.value
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = cfg.get("from_addr", cfg.get("username", ""))
        msg["To"] = to_email
        with smtplib.SMTP(cfg["smtp_host"], cfg.get("smtp_port", 587)) as server:
            if cfg.get("use_tls", True):
                server.starttls()
            if cfg.get("username") and cfg.get("password"):
                server.login(cfg["username"], cfg["password"])
            server.send_message(msg)
        return True
    except Exception:
        return False


def _send_email_test(db: Session, to_email: str) -> bool:
    from backend.app.models import ApplicationSetting
    settings_row = db.query(ApplicationSetting).filter(ApplicationSetting.key == "email").first()
    if not settings_row:
        return False
    cfg = settings_row.value
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText("Test email from Shresth Masala Industry Management System")
        msg["Subject"] = "Test Email"
        msg["From"] = cfg.get("from_addr", cfg.get("username", ""))
        msg["To"] = to_email
        with smtplib.SMTP(cfg["smtp_host"], cfg.get("smtp_port", 587)) as server:
            if cfg.get("use_tls", True):
                server.starttls()
            if cfg.get("username") and cfg.get("password"):
                server.login(cfg["username"], cfg["password"])
            server.send_message(msg)
        return True
    except Exception:
        return False


def _seed_default_footer(db: Session) -> None:
    """Create a default PrintFooter from company.terms if it exists."""
    from backend.app.models import ApplicationSetting, PrintFooter
    terms = _get_setting(db, "company.terms", "")
    if not terms:
        return
    if db.query(PrintFooter).first():
        return
    f = PrintFooter(name="Default", body=terms, is_active=True)
    db.add(f)
    db.commit()


def _default_footer_id(db: Session):
    """Return the footer id from sales.footer_id setting if valid."""
    from backend.app.models import ApplicationSetting, PrintFooter
    val = _get_setting(db, "sales.footer_id", "")
    if not val:
        return None
    try:
        fid = int(val)
    except ValueError:
        return None
    if db.query(PrintFooter).filter(PrintFooter.id == fid, PrintFooter.is_active == True).first():
        return fid
    return None


def _resolve_footer(db: Session, footer_id):
    """Resolve (footer_id, footer_body) for a challan.
    
    Priority:
    1. Explicit footer_id if valid and active
    2. sales.footer_id setting if valid and active
    3. Default footer (first active) if exists
    4. None
    """
    from backend.app.models import PrintFooter
    from fastapi import HTTPException
    if footer_id:
        f = db.query(PrintFooter).filter(PrintFooter.id == footer_id, PrintFooter.is_active == True).first()
        if f:
            return f.id, f.body
        raise HTTPException(status_code=404, detail="Footer not found or inactive")
    fid = _default_footer_id(db)
    if fid:
        f = db.query(PrintFooter).filter(PrintFooter.id == fid).first()
        if f:
            return f.id, f.body
    # Fallback to first active
    f = db.query(PrintFooter).filter(PrintFooter.is_active == True).first()
    if f:
        return f.id, f.body
    return None, ""


def _send_scheduled_report(db: Session, report_type: str) -> bool:
    from backend.app.models import ApplicationSetting
    settings_row = db.query(ApplicationSetting).filter(ApplicationSetting.key == "email").first()
    if not settings_row or not settings_row.value.get("report_to"):
        return False
    cfg = settings_row.value
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        from backend.app.services.excel_export import run_export

        msg = MIMEMultipart()
        msg["Subject"] = f"Shresth Masala - {report_type.replace('_', ' ').title()} Report"
        msg["From"] = cfg.get("from_addr", cfg.get("username", ""))
        msg["To"] = cfg["report_to"]

        if report_type in ("sales_summary", "purchase_summary", "production_summary"):
            result = run_export(db, report_type)
            csv_content = result["content"].decode()
            msg.attach(MIMEText(f"Attached is the {report_type.replace('_', ' ')} report.", "plain"))
            from email.mime.application import MIMEApplication
            attach = MIMEApplication(csv_content.encode(), _subtype="csv")
            attach.add_header("Content-Disposition", "attachment", filename=result["filename"])
            msg.attach(attach)
        else:
            msg.attach(MIMEText(f"Report type {report_type} not yet implemented for email.", "plain"))

        with smtplib.SMTP(cfg["smtp_host"], cfg.get("smtp_port", 587)) as server:
            if cfg.get("use_tls", True):
                server.starttls()
            if cfg.get("username") and cfg.get("password"):
                server.login(cfg["username"], cfg["password"])
            server.send_message(msg)
        return True
    except Exception:
        return False


def _unknown_import_type(import_type: str):
    from fastapi import HTTPException
    return HTTPException(status_code=400, detail=f"Unknown import type: {import_type}")


def _get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(ApplicationSetting).filter(ApplicationSetting.key == key).first()
    return row.value if row else default


def _set_setting(db: Session, key: str, value: str, category: str = "", description: str = "") -> None:
    row = db.query(ApplicationSetting).filter(ApplicationSetting.key == key).first()
    if row:
        row.value = value
    else:
        row = ApplicationSetting(key=key, value=value, category=category, description=description)
        db.add(row)


# Editor Mode exemptions
_EDITOR_MODE_EXEMPT = (
    "/api/dashboard/preferences",
    "/notifications/recent",
    "/settings/company",
)

_EDITOR_MODE_EXEMPT_PREFIX = (
    "/dashboard/shares",
)


def _editor_mode_always_allowed(path: str) -> bool:
    if path in _EDITOR_MODE_EXEMPT:
        return True
    return any(
        path == prefix or path.startswith(prefix + "/")
        for prefix in _EDITOR_MODE_EXEMPT_PREFIX
    )