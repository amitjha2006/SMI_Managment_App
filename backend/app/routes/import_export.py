"""Import/export routes"""
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from sqlalchemy.orm import Session
from fastapi.responses import PlainTextResponse, StreamingResponse

from backend.app.core.database import get_db
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
def import_reports(db: Session = Depends(get_db)):
    """List all import reports."""
    from backend.app.models import ImportReport
    return db.query(ImportReport).order_by(ImportReport.id.desc()).all()


@router.get("/import/undo-status")
def import_undo_status(import_type: str, db: Session = Depends(get_db)):
    """Check if an import can be undone."""
    from backend.app.models import ImportReport
    report = db.query(ImportReport).filter(ImportReport.import_type == import_type).order_by(ImportReport.id.desc()).first()
    if not report:
        return {"can_undo": False, "reason": "No import found for this type"}
    if report.undone:
        return {"can_undo": False, "reason": "Already undone"}
    return {"can_undo": True, "report_id": report.id, "imported_at": report.created_at.isoformat() if report.created_at else None}


@router.post("/import/undo")
def undo_import(payload: dict, request: Request, db: Session = Depends(get_db)):
    """Undo the last import of a given type."""
    import_type = (payload or {}).get("import_type")
    if not import_type:
        raise HTTPException(status_code=400, detail="import_type required")

    from backend.app.models import ImportReport
    report = db.query(ImportReport).filter(ImportReport.import_type == import_type, ImportReport.undone == False).order_by(ImportReport.id.desc()).first()
    if not report:
        raise HTTPException(status_code=404, detail="No import to undo")

    report.undone = True
    report.undone_at = _ist_wall()
    report.undone_by = _request_user_and_role(db, request)[0].id if _request_user_and_role(db, request)[0] else None

    _audit(db, request, "import", "undo", record=report, record_type="import_report", new_values={"import_type": import_type, "undone": True})
    db.commit()
    return {"status": "undone", "import_type": import_type, "report_id": report.id}


@router.post("/import/{import_type}")
def import_data(import_type: str, file: UploadFile = File(...), request: Request = None, db: Session = Depends(get_db)):
    """Import data from Excel file."""
    if import_type not in IMPORTERS:
        raise HTTPException(status_code=400, detail="Unknown import type: " + import_type)

    content = file.file.read()
    try:
        df = read_excel_file(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Failed to read Excel file: " + str(e))

    result = run_import(db, import_type, df, request)
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