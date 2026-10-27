"""Ordering guard + honest status for Excel imports.

On a fresh/branch database the master tables are empty. Importing a dependent
type first (challans before customers, invoices before suppliers/materials)
used to skip every row while still reporting success=True, and the UI never
showed the per-row skip reasons. These tests pin the fixed behaviour:

* an all-skipped result reports success=False with an all_skipped flag
* dependent imports on an empty DB are stopped up front with a warning that
  names the missing masters, instead of row-by-row silent skips
* masters import normally on an empty DB, and dependents work once masters
  exist (recommended order: customers/suppliers/materials/products first)
"""

from io import BytesIO

from openpyxl import Workbook

from backend.app.services.excel_import import ImportResult, run_import


def _xlsx(headers, rows):
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


CUSTOMER_HEADERS = ["Customer Name", "Contact Number"]
CUSTOMER_ROWS = [["Asha Traders", "9876543210"]]

SUPPLIER_HEADERS = ["Supplier Name", "Contact Number"]
SUPPLIER_ROWS = [["Ram Suppliers", "9812345678"]]

MATERIAL_HEADERS = ["Raw Material Name", "Unit"]
MATERIAL_ROWS = [["Red Chilli", "kg"]]

CHALLAN_HEADERS = ["Challan Number", "Customer Name", "Product Name", "Quantity", "Rate per Kg"]
CHALLAN_ROWS = [["CH-00001", "Asha Traders", "Chilli Powder", "10", "200"]]

INVOICE_HEADERS = ["Invoice Number", "Supplier Name", "Raw Material Name", "Quantity", "Rate"]
INVOICE_ROWS = [["INV-00001", "Ram Suppliers", "Red Chilli", "50", "120"]]


def test_all_skipped_summary_is_honest():
    result = ImportResult("sales_challans")
    result.total_rows = 3
    for i in range(3):
        result.add_skipped(i + 2, "Customer not found", {})
    summary = result.summary()
    assert summary["imported"] == 0
    assert summary["skipped"] == result.total_rows
    assert summary["success"] is False
    assert summary["all_skipped"] is True


def test_partial_import_still_reports_success():
    result = ImportResult("customers")
    result.total_rows = 2
    result.imported = 1
    result.add_skipped(3, "Missing name and contact", {})
    summary = result.summary()
    assert summary["success"] is True
    assert summary["all_skipped"] is False


def test_challans_stopped_when_no_customers(db):
    content = _xlsx(CHALLAN_HEADERS, CHALLAN_ROWS)
    summary = run_import(db, "sales_challans", content)
    assert summary["imported"] == 0
    assert summary["skipped"] == len(CHALLAN_ROWS)
    assert summary["success"] is False
    assert "customer" in summary["warning"].lower()


def test_invoices_stopped_when_no_masters(db):
    content = _xlsx(INVOICE_HEADERS, INVOICE_ROWS)
    summary = run_import(db, "purchase_invoices", content)
    assert summary["imported"] == 0
    assert summary["skipped"] == len(INVOICE_ROWS)
    assert summary["success"] is False
    warning = summary["warning"].lower()
    assert "supplier" in warning and "raw material" in warning


def test_masters_import_normally_on_empty_db(db):
    summary = run_import(db, "customers", _xlsx(CUSTOMER_HEADERS, CUSTOMER_ROWS))
    assert summary["imported"] == 1
    assert summary["success"] is True


def test_dependents_work_once_masters_exist(db):
    assert run_import(db, "customers", _xlsx(CUSTOMER_HEADERS, CUSTOMER_ROWS))["imported"] == 1
    challans = run_import(db, "sales_challans", _xlsx(CHALLAN_HEADERS, CHALLAN_ROWS))
    assert challans["imported"] == 1, challans

    assert run_import(db, "suppliers", _xlsx(SUPPLIER_HEADERS, SUPPLIER_ROWS))["imported"] == 1
    assert run_import(db, "raw_materials", _xlsx(MATERIAL_HEADERS, MATERIAL_ROWS))["imported"] == 1
    invoices = run_import(db, "purchase_invoices", _xlsx(INVOICE_HEADERS, INVOICE_ROWS))
    assert invoices["imported"] == 1, invoices


def test_import_type_dropdown_has_no_duplicates():
    # loadTypes() appends server types onto hardcoded <option>s; without a
    # clear-first step every type shows twice.
    import os

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "import.html"), encoding="utf-8") as fh:
        html = fh.read()
    i = html.find("async function loadTypes()")
    assert i >= 0
    body = html[i:i + 1200]
    assert "options.length" in body and "remove(" in body, (
        "type list is appended without clearing: every data type shows twice"
    )
