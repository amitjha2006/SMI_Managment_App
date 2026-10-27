"""Universal timestamps: every table carries its when.

created_at (IST wall, Python-side default) on masters and transactions,
event time (uploaded_at/timestamp/changed_at) where that is the honest
field, business dates on documents. Two deliberate exceptions, and only
two: permissions is static seed data, and sales_invoice_items are lines
whose invoice carries the time.

Run: python -m pytest backend/tests/test_universal_timestamps.py -v
"""

import backend.app.models  # noqa: F401  (registers every table)
from backend.app.core.database import Base

TIME_LIKE = ("created_at", "updated_at", "uploaded_at", "timestamp",
             "changed_at", "archived_at", "period_month", "payment_date",
             "invoice_date", "challan_date", "production_date", "backup_date",
             "joining_date", "start_date", "end_date", "due_date")

# Static reference data with no lifecycle, and lines timed by their parent.
EXCEPTED = {
    "permissions": "static seed catalogue, never user-written",
    "sales_invoice_items": "lines timed by their parent sales invoice",
}


def test_every_table_carries_its_when():
    bare = []
    for name in sorted(Base.metadata.tables):
        cols = {c.name for c in Base.metadata.tables[name].columns}
        if name in EXCEPTED:
            continue
        if not any(t in cols for t in TIME_LIKE):
            bare.append(name)
    assert not bare, f"tables with no timestamp at all: {bare}"


def test_exceptions_stay_minimal():
    tables = set(Base.metadata.tables)
    assert set(EXCEPTED) <= tables, "excepted table renamed or gone - recheck"
    assert len(EXCEPTED) <= 2, f"exception list is growing: {sorted(EXCEPTED)}"
