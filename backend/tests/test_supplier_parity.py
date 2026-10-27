"""Supplier surfaces carry the same details as customer surfaces.

Customer export has Contact Number 2 / Bank Name / Notes; the supplier
export, form and detail views lagged behind even though the model, schemas
and importer already handle all three. These tests pin parity.
"""

from io import BytesIO

from openpyxl import Workbook

from backend.app.models import Supplier
from backend.app.services.excel_export import export_suppliers, run_export
from backend.app.services.excel_import import run_import


def test_supplier_export_has_customer_parity_columns(db):
    _, headers, _ = export_suppliers(db)
    assert "Company Name" in headers
    assert "Contact Number 2" in headers
    assert "Bank Name" in headers
    assert "Notes" in headers


def test_supplier_round_trip_keeps_new_fields(make_db):
    headers = [
        "Supplier Name", "Company Name", "Contact Number", "Contact Number 2",
        "Bank Name", "Notes",
    ]
    rows = [[
        "Ram Suppliers", "Ram & Sons", "9812345678",
        "9812345679", "SBI Bhagalpur", "old dues clear",
    ]]
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    content = buf.read()

    db1, db2 = make_db(), make_db()
    first = run_import(db1, "suppliers", content)
    assert first["imported"] == 1, first

    exported = run_export(db1, "suppliers")["content"]
    second = run_import(db2, "suppliers", exported)
    assert second["imported"] == 1, second

    s = db2.query(Supplier).filter(Supplier.name == "Ram Suppliers").one()
    assert s.company_name == "Ram & Sons"
    assert s.contact_number_2 == "9812345679"
    assert s.bank_detail == "SBI Bhagalpur"
    assert s.notes == "old dues clear"
