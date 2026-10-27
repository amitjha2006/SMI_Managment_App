"""Backup routes"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from datetime import datetime
from pathlib import Path

from backend.app.core.database import get_db
from backend.app.models import BackupRecord
from backend.app.schemas import BackupRecordSchema
from backend.app.core.shared import (
    _audit,
    _require_admin,
    _ist_wall,
    _request_user_and_role,
)

router = APIRouter()

# This function can be monkeypatched in tests
def _backup_dir(db=None):
    """Get the backup directory for the current database.
    
    If a custom backup folder is configured in settings, use that.
    Otherwise, use the default 'backups' directory next to the database file.
    """
    import os
    from backend.app.core.database import engine
    from backend.app.models import ApplicationSetting
    
    db_url = str(engine.url)
    if not db_url.startswith("sqlite:///"):
        return Path("data/backups")
    
    db_path = db_url[10:]  # Remove "sqlite:///"
    
    # Check for custom backup folder setting
    if db is not None:
        backup_setting = db.query(ApplicationSetting).filter(ApplicationSetting.key == "backup").first()
        if backup_setting and backup_setting.value:
            import json
            try:
                cfg = json.loads(backup_setting.value) if isinstance(backup_setting.value, str) else backup_setting.value
                custom_folder = cfg.get("folder")
                if custom_folder:
                    return Path(custom_folder)
            except Exception:
                pass
    
    return Path(os.path.dirname(db_path)) / "backups"


@router.get("/backup/records", response_model=list[BackupRecordSchema])
def list_backup_records(db: Session = Depends(get_db)):
    """List backup records, filtering out those whose files no longer exist."""
    records = db.query(BackupRecord).order_by(BackupRecord.id.desc()).all()
    # Filter out records whose files are gone
    try:
        backup_directory = _backup_dir(db)
    except Exception:
        # If we can't determine backup directory, return all records unfiltered
        return records
    valid_records = []
    for record in records:
        file_path = backup_directory / record.filename
        if file_path.exists():
            valid_records.append(record)
        else:
            # Delete orphaned record
            db.delete(record)
    if len(valid_records) != len(records):
        db.commit()
    return valid_records


@router.post("/backup/create", response_model=BackupRecordSchema)
def create_backup(request: Request, db: Session = Depends(get_db)):
    """Create a database backup."""
    _require_admin(request, db)

    import shutil
    import os
    from backend.app.core.config import settings
    from backend.app.core.database import engine
    from backend.app.models import ApplicationSetting

    # Get database path
    db_url = str(engine.url)
    if db_url.startswith("sqlite:///"):
        db_path = db_url[10:]  # Remove "sqlite:///"
    else:
        raise HTTPException(status_code=500, detail="Backup only supported for SQLite")

    if not os.path.exists(db_path):
        raise HTTPException(status_code=500, detail="Database file not found")

    # Check for custom backup folder setting
    backup_setting = db.query(ApplicationSetting).filter(ApplicationSetting.key == "backup").first()
    custom_folder = None
    if backup_setting and backup_setting.value:
        import json
        try:
            cfg = json.loads(backup_setting.value) if isinstance(backup_setting.value, str) else backup_setting.value
            custom_folder = cfg.get("folder")
        except Exception:
            pass

    # Create backup directory - use custom folder if set, otherwise default next to db
    if custom_folder:
        backup_dir = custom_folder
    else:
        backup_dir = os.path.join(os.path.dirname(db_path), "backups")
    os.makedirs(backup_dir, exist_ok=True)

    # Generate backup filename
    timestamp = _ist_wall().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"masala_factory_{timestamp}.db"
    backup_path = os.path.join(backup_dir, backup_filename)

    # Copy database file (using shutil for atomic copy)
    shutil.copy2(db_path, backup_path)

    # Verify backup integrity
    import sqlite3
    try:
        conn = sqlite3.connect(backup_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check")
        result = cursor.fetchone()
        conn.close()
        if result[0] != "ok":
            os.remove(backup_path)
            raise HTTPException(status_code=500, detail="Backup integrity check failed")
    except Exception as e:
        if os.path.exists(backup_path):
            os.remove(backup_path)
        raise HTTPException(status_code=500, detail=f"Backup verification failed: {e}")

    # Record backup
    file_size = os.path.getsize(backup_path)
    backup_date = _ist_wall()
    record = BackupRecord(
        filename=backup_filename,
        file_path=backup_path,
        file_size=file_size,
        backup_date=backup_date,
        created_by=_request_user_and_role(db, request)[0].id if _request_user_and_role(db, request)[0] else None,
    )
    db.add(record)
    _audit(db, request, "backup", "create", record=record, record_type="backup_record", new_values={"filename": backup_filename, "size": file_size})
    db.commit()
    db.refresh(record)
    return record


@router.get("/backup/download/{record_id}")
def download_backup(record_id: int, request: Request, db: Session = Depends(get_db)):
    """Download a backup file."""
    _require_admin(request, db)
    record = db.query(BackupRecord).filter(BackupRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Backup record not found")

    from backend.app.core.config import settings
    from backend.app.core.database import engine
    db_url = str(engine.url)
    if db_url.startswith("sqlite:///"):
        db_path = db_url[10:]
        backup_dir = os.path.join(os.path.dirname(db_path), "backups")
    else:
        backup_dir = "data/backups"

    backup_path = os.path.join(backup_dir, record.filename)
    if not os.path.exists(backup_path):
        raise HTTPException(status_code=404, detail="Backup file not found")

    return FileResponse(backup_path, filename=record.filename, media_type="application/octet-stream")


@router.post("/backup/restore/{record_id}")
def restore_backup(record_id: int, request: Request, db: Session = Depends(get_db)):
    """Restore database from backup."""
    _require_admin(request, db)
    record = db.query(BackupRecord).filter(BackupRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Backup record not found")

    from backend.app.core.config import settings
    from backend.app.core.database import engine
    import shutil
    import os

    db_url = str(engine.url)
    if db_url.startswith("sqlite:///"):
        db_path = db_url[10:]
        backup_dir = os.path.join(os.path.dirname(db_path), "backups")
    else:
        backup_dir = "data/backups"

    backup_path = os.path.join(backup_dir, record.filename)
    if not os.path.exists(backup_path):
        raise HTTPException(status_code=404, detail="Backup file not found")

    # Verify integrity before restore
    import sqlite3
    try:
        conn = sqlite3.connect(backup_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check")
        result = cursor.fetchone()
        conn.close()
        if result[0] != "ok":
            raise HTTPException(status_code=500, detail="Backup integrity check failed")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backup verification failed: {e}")

    # Close current connections and replace database
    # Note: In production, this would require careful handling of active connections
    # For this offline-first app, we'll copy the backup over the current DB
    try:
        engine.dispose()
        shutil.copy2(backup_path, db_path)
        _audit(db, request, "backup", "restore", record=record, record_type="backup_record", new_values={"restored_from": record.filename})
        db.commit()
        return {"status": "restored", "from": record.filename}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Restore failed: {e}")


@router.delete("/backup/records/{record_id}")
def delete_backup_record(record_id: int, request: Request, db: Session = Depends(get_db)):
    """Delete a backup record (and optionally the file)."""
    _require_admin(request, db)
    record = db.query(BackupRecord).filter(BackupRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Backup record not found")

    # Delete file
    from backend.app.core.config import settings
    from backend.app.core.database import engine
    import os
    db_url = str(engine.url)
    if db_url.startswith("sqlite:///"):
        db_path = db_url[10:]
        backup_dir = os.path.join(os.path.dirname(db_path), "backups")
    else:
        backup_dir = "data/backups"

    backup_path = os.path.join(backup_dir, record.filename)
    if os.path.exists(backup_path):
        os.remove(backup_path)

    _audit(db, request, "backup", "delete", record=record, record_type="backup_record", old_values={"filename": record.filename})
    db.delete(record)
    db.commit()
    return {"status": "deleted", "id": record_id}