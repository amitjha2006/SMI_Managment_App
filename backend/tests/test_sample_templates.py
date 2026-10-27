"""Sample templates import cleanly in the recommended order.

The templates/ files are a linked demo set: suppliers/materials feed
purchase invoices, customers/products feed challans, challans feed
payments. Importing them in README order must succeed row-for-row with
no failures - otherwise the samples teach a flow that does not work.

Run: python -m pytest backend/tests/test_sample_templates.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app.services.excel_import import run_import  # noqa: E402

ORDER = [
    "customers",
    "suppliers",
    "raw_materials",
    "products",
    "purchase_invoices",
    "sales_challans",
    "customer_payments",
    "expenses",
    "employees",
]

EXPECTED_ROWS = {
    "customers": 2,
    "suppliers": 2,
    "raw_materials": 3,
    "products": 4,
    # Line items roll up: 3 rows make 2 invoices, 3 rows make 2 challans.
    "purchase_invoices": 2,
    "sales_challans": 2,
    "customer_payments": 1,
    "expenses": 2,
    "employees": 2,
}


def _content(name):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "templates", f"sample_{name}.xlsx"), "rb") as fh:
        return fh.read()


def test_every_type_has_a_template():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for name in ORDER:
        assert os.path.exists(os.path.join(root, "templates", f"sample_{name}.xlsx")), name


def test_full_set_imports_in_order(db):
    for name in ORDER:
        summary = run_import(db, name, _content(name))
        assert summary["failed"] == 0, (name, summary)
        assert summary["imported"] == EXPECTED_ROWS[name], (name, summary)
        assert summary["success"] is True, (name, summary)


def test_payment_fits_challan_outstanding(db):
    for name in ORDER:
        run_import(db, name, _content(name))
    from backend.app import models as _models  # noqa: E402

    ch = db.query(_models.SalesChallan).filter(
        _models.SalesChallan.challan_number == "CH-2026-002").one()
    assert float(ch.outstanding_amount) == 1180.0 - 500.0, ch.outstanding_amount
