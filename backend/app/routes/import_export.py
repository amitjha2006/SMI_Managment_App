"""Import/export routes"""
import asyncio
import json
import shutil
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy.orm import Session

from backend.app.core.database import get_db, _live_db_path, _do_backup
from backend.app.models import (
    Customer,
    CustomerPayment,
    Employee,
    Expense,
    Product,
    PurchaseInvoice,
    RawMaterial,
    SalesChallan,
    Supplier,
    BackupRecord,
    ImportReport,
    AuditLog,
)
from backend.app.core.shared import (
    _audit,
    _require_admin,
    _ist_wall,
    _request_user_and_role,
)
from backend.app.services.excel_import import IMPORTERS, read_excel_file, run_import

router = APIRouter()


@router.get("/import/types")
def import_types():
    """Available import types in dependency order."""
    return [
        {"key": "customers", "label": "Customers", "order": 1},
        {"key": "suppliers", "label": "Suppliers", "order": 2},
        {"key": "raw_materials", "label": "Raw Materials", "order": 3},
        {"key": "products", "label": "Products", "order": 4},
        {"key": "purchase_invoices", "label": "Purchase Invoices", "order": 5},
        {"key": "sales_challans", "label": "Sales Challans", "order": 6},
        {"key": "customer_payments", "label": "Customer Payments", "order": 7},
        {"key": "expenses", "label": "Expenses", "order": 8},
        {"key": "employees", "label": "Employees", "order": 9},
    ]


@router.get("/import/reports")
def import_reports(db: Session = Depends(get_db), limit: int | None = None):
    """List all import reports."""
    from backend.app.models import ImportReport
    q = db.query(ImportReport).order_by(ImportReport.id.desc()).limit(20)
    return [
        {
            "id": r.id,
            "import_type": r.import_type,
            "filename": r.filename,
            "total_rows": r.total_rows,
            "imported": r.imported,
            "skipped": r.skipped,
            "failed": r.failed,
            "errors": json.loads(r.errors) if r.errors else [],
            "skipped_rows": json.loads(r.skipped_rows) if r.skipped_rows else [],
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in q.all()
    ]


@router.get("/import/undo-status")
def import_undo_status(db: Session = Depends(get_db), import_type: str | None = None):
    """Check if an import can be undone."""
    import json
    q = db.query(AuditLog).filter(AuditLog.module == "import")
    if import_type:
        q = q.filter(AuditLog.record_type == f"import_{import_type}")
    report = q.order_by(AuditLog.id.desc()).first()
    if not report:
        return {"available": False, "reason": "No import found"}
    try:
        vals = json.loads(report.new_values or "{}")
    except Exception:
        vals = {}
    undone = bool(report.notes and "undone" in report.notes.lower())
    if undone:
        return {"available": False, "reason": "Already undone"}
    backup_id = vals.get("pre_import_backup_id")
    backup_filename = vals.get("pre_import_backup", "")
    if not backup_id:
        return {"available": False, "reason": "No safety copy"}
    backup = db.query(BackupRecord).filter(BackupRecord.id == backup_id).first()
    if not backup or not Path(backup.file_path).exists():
        return {"available": False, "reason": "Backup file missing"}
    return {
        "available": True,
        "import_type": (report.record_type or "").replace("import_", "") or import_type or "unknown",
        "report_id": report.id,
        "imported_file": vals.get("filename", ""),
        "imported": vals.get("imported", 0),
        "backup_id": backup_id,
        "backup_filename": backup_filename,
    }


@router.post("/import/prune-backups")
def _prune_pre_import_backups(db: Session = Depends(get_db)):
    """Keep only the newest five pre-import safety copies; delete the rest."""
    rows = (
        db.query(BackupRecord)
        .filter(BackupRecord.filename.like("pre-import_%"))
        .order_by(BackupRecord.backup_date.desc(), BackupRecord.id.desc())
        .all()
    )
    for row in rows[5:]:
        try:
            Path(row.file_path).unlink(missing_ok=True)
        except Exception:
            pass
        db.delete(row)
    db.commit()
    return {"pruned": len(rows) - 5 if len(rows) > 5 else 0}


@router.post("/import/undo")
def undo_last_import(request: Request, db: Session = Depends(get_db), payload: dict = None):
    """Undo the last import of a given type."""
    import_type = (payload or {}).get("import_type")
    import json
    q = db.query(AuditLog).filter(AuditLog.module == "import")
    if import_type:
        q = q.filter(AuditLog.record_type == f"import_{import_type}")
    report = q.order_by(AuditLog.id.desc()).first()
    if not report:
        raise HTTPException(status_code=404, detail="No import to undo")

    # Restore the pre-import safety copy over the live DB
    try:
        vals = json.loads(report.new_values or "{}")
    except Exception:
        vals = {}
    backup_id = vals.get("pre_import_backup_id")
    backup_filename = vals.get("pre_import_backup", "")
    if backup_id:
        backup = db.query(BackupRecord).filter(BackupRecord.id == backup_id).first()
        if backup and Path(backup.file_path).exists():
            shutil.copy2(backup.file_path, _live_db_path())

    report.notes = (report.notes or "") + " undone"
    _audit(db, request, "import", "undo", record=report, record_type="import_report",
           new_values={"import_type": import_type, "undone": True})
    db.commit()
    return {"status": "undone", "import_type": import_type, "report_id": report.id}


def _save_import_report(db: Session, import_type: str, filename: str, result: dict, report=None):
    """Persist an import result row (errors + skipped_rows serialized as JSON).

    Updates `report` if given, otherwise creates a new row. Raises on failure
    so the caller can convert it to a 500.
    """
    if report is None:
        report = ImportReport(
            import_type=import_type,
            filename=filename,
        )
        db.add(report)
    report.imported = result.get("imported", 0)
    report.skipped = result.get("skipped", 0)
    report.failed = result.get("failed", 0)
    report.errors = json.dumps(result.get("errors", []))
    report.skipped_rows = json.dumps(result.get("skipped_rows", []), default=str)
    db.commit()
    db.refresh(report)
    return report


@router.post("/import/{import_type}")
async def import_data(import_type: str, request: Request = None, file: UploadFile = File(None), sheet: str | None = None, db: Session = Depends(get_db)):
    """Import data from Excel file, taking a pre-import safety copy first."""
    if import_type not in IMPORTERS:
        raise HTTPException(status_code=400, detail="Unknown import type: " + import_type)

    import sys
    main_mod = sys.modules.get("backend.main")
    _do_backup_fn = getattr(main_mod, "_do_backup", None) if main_mod else None
    if _do_backup_fn is None:
        from backend.app.core.database import _do_backup as _do_backup_fn
    _run_import_fn = getattr(main_mod, "run_import", None) if main_mod else None
    if _run_import_fn is None:
        from backend.app.services.excel_import import run_import as _run_import_fn
    _save_import_report_fn = getattr(main_mod, "_save_import_report", None) if main_mod else None

    # Pre-import safety copy
    backup_rec = _do_backup_fn(
        db, is_auto=False,
        description=f"Pre-import {import_type} {getattr(file, 'filename', '')}",
        prefix="pre-import_",
    )

    if file is None:
        raise HTTPException(status_code=400, detail="file required")
    if hasattr(file, "file"):
        content = file.file.read()
    else:
        content = file.read()
    if asyncio.iscoroutine(content):
        content = await content

    # Persist the import report row first so a failed import still leaves a record.
    from backend.app.models import ImportReport as _IR
    report = _IR(
        import_type=import_type,
        filename=getattr(file, "filename", "upload"),
        imported=0,
    )
    db.add(report)
    db.commit()

    try:
        result = _run_import_fn(db, import_type, content, sheet)
        result["status"] = "success"
        result["pre_import_backup"] = backup_rec.filename
        report.imported = result.get("imported", 0)
        db.commit()
    except Exception as e:
        report.failed = 1
        report.errors = json.dumps([{"message": str(e)}])
        db.commit()
        raise HTTPException(status_code=500, detail="Import failed: " + str(e))

    # Persist the full import report (errors + skipped_rows). If this fails,
    # the import still happened but we report it as a 500 so the frontend
    # can surface the problem instead of a silent blank.
    try:
        if _save_import_report_fn is not None:
            _save_import_report_fn(
                db, import_type, getattr(file, "filename", "upload"), result, report
            )
        db.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail="recording the result failed: " + str(e))

    _audit(db, request, "import", "create", record_type="import_report",
           new_values={"import_type": import_type, "pre_import_backup_id": backup_rec.id, "pre_import_backup": backup_rec.filename, "imported": result.get("imported", 0)})
    return result


@router.post("/import/preview/{import_type}")
def preview_import(import_type: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Preview import without committing."""
    if import_type not in IMPORTERS:
        raise HTTPException(status_code=400, detail="Unknown import type: " + import_type)

    content = file.file.read()
    try:
        df = read_excel_file(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Failed to read Excel file: " + str(e))

    importer = IMPORTERS[import_type]
    errors = []
    preview = []
    for idx, row in df.iterrows():
        row_num = idx + 2
        try:
            validated = importer.validate_row(row)
            preview.append(validated)
        except Exception as e:
            errors.append({"row": row_num, "error": str(e)})

    return {
        "import_type": import_type,
        "total_rows": len(df),
        "valid_rows": len(preview),
        "errors": errors,
        "preview": preview[:10],
    }


@router.get("/export/{export_type}")
def export_data(export_type: str, db: Session = Depends(get_db)):
    """Export data to CSV."""
    import csv
    import io
    from datetime import datetime

    if export_type == "customers":
        rows = db.query(Customer).filter(Customer.is_active == True).all()
        fieldnames = ["id", "name", "phone", "address", "gstin", "email", "is_active"]
        data = [{f: getattr(r, f) for f in fieldnames} for r in rows]
    elif export_type == "suppliers":
        rows = db.query(Supplier).filter(Supplier.is_active == True).all()
        fieldnames = ["id", "name", "phone", "address", "gstin", "email", "is_active"]
        data = [{f: getattr(r, f) for f in fieldnames} for r in rows]
    elif export_type == "raw_materials":
        rows = db.query(RawMaterial).filter(RawMaterial.is_active == True).all()
        fieldnames = ["id", "name", "category_id", "unit", "current_stock", "threshold_stock", "watch_stock", "hsn_code"]
        data = [{f: getattr(r, f) for f in fieldnames} for r in rows]
    elif export_type == "products":
        rows = db.query(Product).filter(Product.is_active == True).all()
        fieldnames = ["id", "name", "category_id", "hsn_code", "gst_rate", "default_rate", "formulation"]
        data = [{f: getattr(r, f) for f in fieldnames} for r in rows]
    elif export_type == "purchase_invoices":
        rows = db.query(PurchaseInvoice).filter(PurchaseInvoice.is_archived == False).all()
        fieldnames = ["id", "invoice_number", "supplier_id", "date", "transport_charges", "other_charges", "total_amount", "status"]
        data = [{f: getattr(r, f) for f in fieldnames} for r in rows]
    elif export_type == "sales_challans":
        rows = db.query(SalesChallan).filter(SalesChallan.is_archived == False).all()
        fieldnames = ["id", "challan_number", "customer_id", "date", "transport_charges", "other_charges", "total_amount", "status"]
        data = [{f: getattr(r, f) for f in fieldnames} for r in rows]
    elif export_type == "customer_payments":
        rows = db.query(CustomerPayment).all()
        fieldnames = ["id", "challan_id", "date", "amount", "mode", "reference"]
        data = [{f: getattr(r, f) for f in fieldnames} for r in rows]
    elif export_type == "expenses":
        rows = db.query(Expense).filter(Expense.is_archived == False).all()
        fieldnames = ["id", "category_id", "date", "description", "amount", "payment_mode", "reference"]
        data = [{f: getattr(r, f) for f in fieldnames} for r in rows]
    elif export_type == "employees":
        rows = db.query(Employee).filter(Employee.is_active == True).all()
        fieldnames = ["id", "name", "role", "phone", "address", "aadhaar", "pan", "bank_account", "ifsc", "basic_salary", "hra", "allowance", "deduction", "joining_date"]
        data = [{f: getattr(r, f) for f in fieldnames} for r in rows]
    else:
        raise HTTPException(status_code=400, detail="Unknown export type: " + export_type)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(data)

    return StreamingResponse(
        io.BytesIO(output.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=" + export_type + "_" + datetime.now().strftime("%Y%m%d") + ".csv"}
    )