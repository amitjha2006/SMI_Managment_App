"""Purchase-invoice import resolves suppliers by company name too.

Run: python -m pytest backend/tests/test_supplier_company_import.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app import models  # noqa: E402
from backend.app.models.raw_material import UnitType  # noqa: E402
from backend.app.services.excel_import import run_import  # noqa: E402


def _supplier(db, name="Ramesh", company="Malwa Agro Foods"):
    s = models.Supplier(
        supplier_id="SUP-9001", name=name, company_name=company,
        contact_number="9876500002",
    )
    db.add(s)
    db.commit()
    return s


def _material(db):
    m = models.RawMaterial(
        material_id="RM-9001", name="Coriander", unit=UnitType.KG,
    )
    db.add(m)
    db.commit()
    return m


def _invoice_xlsx(supplier_value, extra_header=None):
    import io

    from openpyxl import Workbook

    headers = ["Invoice Number", "Invoice Date", "Supplier Name",
               "Raw Material Name", "Quantity", "Rate"]
    if extra_header:
        headers.append(extra_header[0])
    row = ["PUR-COMP-1", "2026-09-12", supplier_value, "Coriander", 10, 68]
    if extra_header:
        row.append(extra_header[1])
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_invoice_matches_company_in_supplier_name_column(db, make_db):
    _ = make_db
    _supplier(db)
    _material(db)
    out = run_import(db, "purchase_invoices", _invoice_xlsx("Malwa Agro Foods"))
    assert out["imported"] == 1, out
    inv = db.query(models.PurchaseInvoice).filter(
        models.PurchaseInvoice.invoice_number == "PUR-COMP-1").first()
    assert inv is not None and inv.supplier.company_name == "Malwa Agro Foods"


def test_invoice_matches_explicit_company_column(db, make_db):
    _ = make_db
    _supplier(db)
    _material(db)
    out = run_import(db, "purchase_invoices", _invoice_xlsx(
        "Nobody Matching", extra_header=("Company Name", "Malwa Agro Foods")))
    assert out["imported"] == 1, out


def test_invoice_still_matches_person_name(db, make_db):
    _ = make_db
    _supplier(db)
    _material(db)
    out = run_import(db, "purchase_invoices", _invoice_xlsx("Ramesh"))
    assert out["imported"] == 1, out
