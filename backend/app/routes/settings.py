"""Settings, backup, system info routes"""
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
    _require_admin,
    _ist_wall,
    _request_user_and_role,
    _send_email,
    _send_email_test,
    _send_scheduled_report,
)

router = APIRouter()


# System info
@router.get("/system/info")
def system_info():
    """System information for diagnostics."""
    import sys
    import platform
    import os
    from backend.app.core.database import engine
    return {
        "python_version": sys.version,
        "platform": platform.platform(),
        "database_url": str(engine.url).replace(str(engine.url.password or ""), "***"),
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
    setting.value = {
        "name": payload.get("name", "Shresth Masala Industry"),
        "address": payload.get("address", ""),
        "phone": payload.get("phone", ""),
        "email": payload.get("email", ""),
        "gstin": payload.get("gstin", ""),
        "logo_path": payload.get("logo_path", ""),
    }
    _audit(db, request, "settings", "company_update", record=setting, record_type="application_setting", new_values=setting.value)
    db.commit()
    return setting.value


# Settings - session
@router.get("/settings/session")
def get_session_settings(db: Session = Depends(get_db)):
    setting = db.query(ApplicationSetting).filter(ApplicationSetting.key == "session").first()
    if not setting:
        return {"timeout_minutes": 120}
    return setting.value


@router.put("/settings/session")
def update_session_settings(payload: dict, request: Request, db: Session = Depends(get_db)):
    _require_admin(request, db)
    setting = db.query(ApplicationSetting).filter(ApplicationSetting.key == "session").first()
    if not setting:
        setting = ApplicationSetting(key="session", value={})
        db.add(setting)
    setting.value = {
        "timeout_minutes": payload.get("timeout_minutes", 120),
    }
    _audit(db, request, "settings", "session_update", record=setting, record_type="application_setting", new_values=setting.value)
    db.commit()
    return setting.value


# Settings - email
@router.get("/settings/email")
def get_email_settings(db: Session = Depends(get_db)):
    setting = db.query(ApplicationSetting).filter(ApplicationSetting.key == "email").first()
    if not setting:
        return {"smtp_host": "", "smtp_port": 587, "smtp_user": "", "smtp_password": "", "from_email": "", "from_name": "", "use_tls": True, "alert_recipient": ""}
    return setting.value


@router.put("/settings/email")
def update_email_settings(payload: dict, request: Request, db: Session = Depends(get_db)):
    _require_admin(request, db)
    setting = db.query(ApplicationSetting).filter(ApplicationSetting.key == "email").first()
    if not setting:
        setting = ApplicationSetting(key="email", value={})
        db.add(setting)
    # Don't log password in audit
    value = {
        "smtp_host": payload.get("smtp_host", ""),
        "smtp_port": payload.get("smtp_port", 587),
        "smtp_user": payload.get("smtp_user", ""),
        "smtp_password": payload.get("smtp_password", ""),
        "from_email": payload.get("from_email", ""),
        "from_name": payload.get("from_name", ""),
        "use_tls": payload.get("use_tls", True),
        "alert_recipient": payload.get("alert_recipient", ""),
    }
    setting.value = value
    # Audit without password
    audit_value = {**value, "smtp_password": "***"}
    _audit(db, request, "settings", "email_update", record=setting, record_type="application_setting", new_values=audit_value)
    db.commit()
    return value


@router.post("/settings/email/preview")
def preview_email(payload: dict, request: Request, db: Session = Depends(get_db)):
    """Preview an email template with sample data."""
    template = (payload or {}).get("template")
    if not template:
        raise HTTPException(status_code=400, detail="template required")
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