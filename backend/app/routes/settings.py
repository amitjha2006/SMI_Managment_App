"""Settings, backup, system info routes"""
import json
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from datetime import datetime

from backend.app.core.database import get_db
from backend.app.models import (
    ApplicationSetting,
    BackupRecord,
)
from backend.app.schemas import BackupRecordSchema
from backend.app.core.shared import (
    _audit,
    _change_reason,
    _get_setting,
    _require_admin,
    _ist_wall,
    _request_user_and_role,
    _send_email,
    _send_email_test,
    _send_scheduled_report,
    _set_setting,
)

router = APIRouter()


# System info
@router.get("/system/info")
def system_info():
    """System information for diagnostics."""
    import sys
    import platform
    import os
    from pathlib import Path
    from backend.app.core.database import engine
    # App version from repo-root VERSION file (next to exe when frozen)
    app_version = "0.1.0"
    for cand in (Path.cwd() / "VERSION", Path(sys.executable).parent / "VERSION"):
        try:
            v = cand.read_text(encoding="utf-8").strip()
            if v:
                app_version = v
                break
        except Exception:
            pass
    # Database file path and size
    db_path = str(engine.url)
    db_size = 0
    try:
        if engine.url.drivername.startswith("sqlite"):
            import os as _os
            db_file = engine.url.database
            if _os.path.exists(db_file):
                db_size = _os.path.getsize(db_file)
    except Exception:
        pass
    return {
        "app": settings.APP_NAME,
        "version": app_version,
        "database": engine.url.drivername,
        "database_path": db_path,
        "database_size": db_size,
        "server": platform.platform(),
        "offline_mode": "true",
        "app_env": os.environ.get("APP_ENV", "production"),
    }


# Settings - backup
@router.get("/settings/backup")
def get_backup_settings(db: Session = Depends(get_db)):
    import json
    setting = db.query(ApplicationSetting).filter(ApplicationSetting.key == "backup").first()
    if not setting:
        return {"schedule_hour": 2, "retention_days": 30, "enabled": True}
    try:
        return json.loads(setting.value) if isinstance(setting.value, str) else setting.value
    except Exception:
        return {"schedule_hour": 2, "retention_days": 30, "enabled": True}


@router.put("/settings/backup")
def update_backup_settings(payload: dict, request: Request, db: Session = Depends(get_db)):
    _require_admin(request, db)
    import json
    import os
    setting = db.query(ApplicationSetting).filter(ApplicationSetting.key == "backup").first()
    if not setting:
        setting = ApplicationSetting(key="backup", value={})
        db.add(setting)
    
    # Validate folder path if provided
    folder = payload.get("folder")
    if folder is not None:
        if folder and not os.path.isabs(folder):
            raise HTTPException(status_code=400, detail="Backup folder must be an absolute path")
        # Ensure directory exists
        if folder:
            os.makedirs(folder, exist_ok=True)
    
    value = {
        "schedule_hour": payload.get("schedule_hour", 2),
        "retention_days": payload.get("retention_days", 30),
        "enabled": payload.get("enabled", True),
    }
    if folder is not None:
        value["folder"] = folder
    
    setting.value = json.dumps(value)
    _audit(db, request, "settings", "backup_update", record=setting, record_type="application_setting", new_values=value)
    db.commit()
    return value


# Settings - sales invoice
@router.get("/settings/sales-invoice")
def get_sales_invoice_settings(db: Session = Depends(get_db)):
    setting = db.query(ApplicationSetting).filter(ApplicationSetting.key == "sales_invoice").first()
    if not setting:
        return {"prefix": "SI", "next_number": 1, "terms": "", "footer_id": None}
    return setting.value


@router.put("/settings/sales-invoice")
def update_sales_invoice_settings(payload: dict, request: Request, db: Session = Depends(get_db)):
    _require_admin(request, db)
    setting = db.query(ApplicationSetting).filter(ApplicationSetting.key == "sales_invoice").first()
    if not setting:
        setting = ApplicationSetting(key="sales_invoice", value={})
        db.add(setting)
    setting.value = {
        "prefix": payload.get("prefix", "SI"),
        "next_number": payload.get("next_number", 1),
        "terms": payload.get("terms", ""),
        "footer_id": payload.get("footer_id"),
    }
    _audit(db, request, "settings", "sales_invoice_update", record=setting, record_type="application_setting", new_values=setting.value)
    db.commit()
    return setting.value


# Settings - company
@router.get("/settings/company")
def get_company_settings(db: Session = Depends(get_db)):
    setting = db.query(ApplicationSetting).filter(ApplicationSetting.key == "company").first()
    if not setting:
        return {"name": "Shresth Masala Industry", "address": "", "phone": "", "email": "", "gstin": "", "logo_path": ""}
    return setting.value


@router.put("/settings/company")
def update_company_settings(payload: dict, request: Request, db: Session = Depends(get_db)):
    # Company settings are admin-only by role (rbac_guard), but Editor Mode exempt for GET
    setting = db.query(ApplicationSetting).filter(ApplicationSetting.key == "company").first()
    if not setting:
        setting = ApplicationSetting(key="company", value={})
        db.add(setting)
    setting.value = json.dumps({
        "name": payload.get("name", "Shresth Masala Industry"),
        "address": payload.get("address", ""),
        "phone": payload.get("phone", ""),
        "email": payload.get("email", ""),
        "gstin": payload.get("gstin", ""),
        "logo_path": payload.get("logo_path", ""),
    })
    _audit(db, request, "settings", "company_update", record=setting, record_type="application_setting", new_values=setting.value)
    db.commit()
    return setting.value


# Settings - session
@router.get("/settings/session")
def get_session_settings(db: Session = Depends(get_db)):
    setting = db.query(ApplicationSetting).filter(ApplicationSetting.key == "session").first()
    if not setting:
        return {"lock_minutes": 15}
    return json.loads(setting.value) if isinstance(setting.value, str) else setting.value


@router.put("/settings/session")
def update_session_settings(payload: dict, request: Request, db: Session = Depends(get_db)):
    _require_admin(request, db)
    raw = payload.get("lock_minutes", 15)
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="lock_minutes must be a number")
    if minutes < 0 or minutes > 240:
        raise HTTPException(status_code=400, detail="lock_minutes must be between 0 and 240")
    setting = db.query(ApplicationSetting).filter(ApplicationSetting.key == "session").first()
    if not setting:
        setting = ApplicationSetting(key="session", value=json.dumps({}))
        db.add(setting)
    setting.value = json.dumps({"lock_minutes": minutes})
    _audit(db, request, "settings", "session_update", record=setting, record_type="application_setting", new_values={"lock_minutes": minutes})
    db.commit()
    return {"lock_minutes": minutes}


# Settings - email
@router.get("/settings/email")
def get_email_settings(db: Session = Depends(get_db)):
    from backend.app.routes.reports import REPORT_SECTION_CHOICES
    setting = db.query(ApplicationSetting).filter(ApplicationSetting.key == "email").first()
    if not setting:
        return {"smtp_host": "", "smtp_port": 587, "smtp_user": "", "smtp_password": "", "from_email": "", "from_name": "", "use_tls": True, "alert_recipient": "", "report_sections": list(REPORT_SECTION_CHOICES)}
    value = setting.value
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = {}
    if not isinstance(value, dict):
        value = {}
    value.setdefault("report_sections", list(REPORT_SECTION_CHOICES))
    return value


@router.put("/settings/email")
def update_email_settings(payload: dict, request: Request, db: Session = Depends(get_db)):
    # Validate report weekday before auth so invalid input returns 400, not 403.
    if "report_weekday" in payload:
        try:
            wd = int(payload["report_weekday"])
            if wd < 0 or wd > 6:
                raise ValueError
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="report_weekday must be 0-6")
    # Validate report_sections before auth so invalid input returns 400, not 403.
    if "report_sections" in payload:
        from backend.app.routes.reports import REPORT_SECTION_CHOICES
        sections = payload["report_sections"]
        if not isinstance(sections, list) or len(sections) == 0:
            raise HTTPException(status_code=400, detail="report_sections must be a non-empty list")
        bad = [s for s in sections if s not in REPORT_SECTION_CHOICES]
        if bad:
            raise HTTPException(status_code=400, detail=f"Unknown report sections: {bad}")

    smtp_fields = {"smtp_host", "smtp_port", "smtp_user", "smtp_password",
                   "from_email", "from_name", "use_tls", "alert_recipient"}
    has_smtp = any(f in payload for f in smtp_fields)
    # Report-only settings (sections, frequency, weekday) don't require admin;
    # SMTP settings do.
    if has_smtp:
        _require_admin(request, db)
    setting = db.query(ApplicationSetting).filter(ApplicationSetting.key == "email").first()
    if not setting:
        setting = ApplicationSetting(key="email", value={})
        db.add(setting)
    existing = setting.value if isinstance(setting.value, dict) else {}
    # Don't log password in audit
    value = {
        "smtp_host": payload.get("smtp_host", existing.get("smtp_host", "")),
        "smtp_port": payload.get("smtp_port", existing.get("smtp_port", 587)),
        "smtp_user": payload.get("smtp_user", existing.get("smtp_user", "")),
        "smtp_password": payload.get("smtp_password", existing.get("smtp_password", "")),
        "from_email": payload.get("from_email", existing.get("from_email", "")),
        "from_name": payload.get("from_name", existing.get("from_name", "")),
        "use_tls": payload.get("use_tls", existing.get("use_tls", True)),
        "alert_recipient": payload.get("alert_recipient", existing.get("alert_recipient", "")),
    }
    if "report_sections" in payload:
        value["report_sections"] = payload["report_sections"]
    elif "report_sections" in existing:
        value["report_sections"] = existing["report_sections"]
    else:
        from backend.app.routes.reports import REPORT_SECTION_CHOICES
        value["report_sections"] = list(REPORT_SECTION_CHOICES)
    setting.value = json.dumps(value)
    # Audit without password
    audit_value = {**value, "smtp_password": "***"}
    _audit(db, request, "settings", "email_update", record=setting, record_type="application_setting", new_values=audit_value)
    db.commit()
    return value


@router.post("/settings/email/preview")
def preview_email(payload: dict, request: Request, db: Session = Depends(get_db)):
    """Preview an email template with sample data, or a daily report for selected sections."""
    template = (payload or {}).get("template")
    sections = (payload or {}).get("sections")
    if template:
        # Simple template rendering - replace {{variable}} with sample values
        sample = {
            "company_name": "Shresth Masala Industry",
            "challan_number": "CH-2026-0042",
            "customer_name": "Sample Customer",
            "date": datetime.now().strftime("%d-%m-%Y"),
            "total_amount": "1,23,456.00",
        }
        for key, val in sample.items():
            template = template.replace(f"{{{{{key}}}}}", val)
        return {"preview": template}
    if sections:
        from backend.app.routes.reports import _build_daily_report
        subject, text, html_body = _build_daily_report(db, sections)
        return {
            "subject": subject,
            "to": "(recipients)",
            "body": text,
            "html": html_body,
        }
    raise HTTPException(status_code=400, detail="template required")


@router.post("/settings/email/test")
def test_email(payload: dict, request: Request, db: Session = Depends(get_db)):
    """Send a test email."""
    _require_admin(request, db)
    to_email = (payload or {}).get("to_email")
    if not to_email:
        raise HTTPException(status_code=400, detail="to_email required")
    success = _send_email_test(db, to_email)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to send test email")
    return {"status": "sent", "to": to_email}


@router.post("/settings/email/send-report")
def send_scheduled_report_endpoint(payload: dict, request: Request, db: Session = Depends(get_db)):
    """Manually trigger a scheduled report email."""
    _require_admin(request, db)
    report_type = (payload or {}).get("report_type")
    if not report_type:
        raise HTTPException(status_code=400, detail="report_type required")
    success = _send_scheduled_report(db, report_type)
    return {"status": "sent" if success else "failed", "report_type": report_type}


def _report_tick(db: Session) -> str:
    """Send the scheduled report if due. Returns 'sent' or 'waiting'.

    Weekly frequency only sends on the configured weekday, once per week.
    Daily sends whenever enabled.
    """
    enabled = _get_setting(db, "email.report_enabled")
    if enabled != "true":
        return "waiting"
    frequency = _get_setting(db, "email.report_frequency")
    weekday = _get_setting(db, "email.report_weekday")
    now = _ist_wall()

    if frequency == "weekly":
        if not weekday:
            return "waiting"
        try:
            weekday_int = int(weekday)
        except (TypeError, ValueError):
            return "waiting"
        if now.weekday() != weekday_int:
            return "waiting"
        # Once per week: skip if already sent this ISO week.
        last_sent = _get_setting(db, "email.report_last_sent")
        if last_sent:
            try:
                last_dt = datetime.fromisoformat(last_sent)
                if last_dt.isocalendar()[:2] == now.isocalendar()[:2]:
                    return "waiting"
            except Exception:
                pass

    to_email = _get_setting(db, "email.report_to")
    if not to_email:
        return "waiting"
    _send_email(db, to_email, "Scheduled Report", "Scheduled report from Shresth Masala Industry")
    _set_setting(db, "email.report_last_sent", now.isoformat())
    db.commit()
    return "sent"