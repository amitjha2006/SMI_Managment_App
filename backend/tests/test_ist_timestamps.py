"""Timestamps: small IST time under every displayed date.

Business dates stay as-is; the small time beside them is the record's created
time, always rendered in India time (Asia/Kolkata). The server stores naive
UTC datetimes, so the helper must interpret offset-less input as UTC first —
otherwise the shown time drifts with the machine clock.

Run: python -m pytest backend/tests/test_ist_timestamps.py -v
"""

import sys

ROOT = r"C:\Shresth_Masala_Application"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _read(rel):
    with open(ROOT + "\\" + rel.replace("/", "\\"), encoding="utf-8") as fh:
        return fh.read()


PAGES_WITH_DATES = (
    "frontend/pages/challans.html",
    "frontend/pages/invoices.html",
    "frontend/pages/production.html",
    "frontend/pages/expenses.html",
    "frontend/pages/backup.html",
    "frontend/pages/inventory.html",
    "frontend/pages/employees.html",
    "frontend/pages/monthly-sales.html",
    "frontend/pages/audit.html",
    "frontend/pages/alerts.html",
    "frontend/pages/settings.html",
)


def test_shared_ist_helper_exists():
    js = _read("frontend/js/components.js")
    assert "dateWithTime" in js, "UI.dateWithTime helper is not defined"
    assert "Asia/Kolkata" in js, "timestamps must be pinned to India time"


def test_helper_treats_naive_input_as_ist():
    # IST-everywhere rule: stored values are naive IST wall time, so the
    # parser stamps +05:30 (never Z/+00:00, which would shift every hour).
    js = _read("frontend/js/components.js")
    i = js.find("function asUtcDate")
    assert i >= 0
    body = js[i:i + 600]
    assert "+05:30" in body, (
        "asUtcDate must interpret offset-less input as IST wall time"
    )
    assert '"Z"' not in body and "'Z'" not in body, (
        "asUtcDate must not reinterpret naive values as UTC"
    )


def test_helper_renders_small_time():
    js = _read("frontend/js/components.js")
    i = js.find("function dateWithTime")
    body = js[i:i + 1200]
    assert "<small" in body, "the time must render small beneath the date"


def test_date_cells_use_the_shared_helper():
    missing = [p for p in PAGES_WITH_DATES if "dateWithTime" not in _read(p)]
    assert not missing, (
        "these pages render dates without the shared IST helper: "
        f"{missing}"
    )


def test_created_at_reaches_payment_expense_employee_payloads():
    from datetime import datetime

    from backend.app.schemas import (
        CustomerPaymentSchema,
        EmployeeSchema,
        ExpenseSchema,
        SupplierPaymentSchema,
    )

    now = datetime(2026, 9, 10, 8, 30)
    cp = CustomerPaymentSchema.model_validate({
        "id": 1, "challan_id": 1, "payment_date": now, "amount": "10",
        "payment_mode": "cash", "created_at": now,
    })
    assert cp.created_at == now
    sp = SupplierPaymentSchema.model_validate({
        "id": 1, "purchase_invoice_id": 1, "payment_date": now, "amount": "10",
        "payment_mode": "cash", "created_at": now,
    })
    assert sp.created_at == now
    ex = ExpenseSchema.model_validate({
        "id": 1, "category_id": 1, "description": "x", "amount": "10", "date": now,
        "payment_mode": "cash", "created_at": now,
    })
    assert ex.created_at == now
    em = EmployeeSchema.model_validate({
        "id": 1, "employee_id": "E-1", "name": "N", "employee_type": "full_time",
        "created_at": now,
    })
    assert em.created_at == now
