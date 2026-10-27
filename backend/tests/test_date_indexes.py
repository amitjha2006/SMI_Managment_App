"""Date indexes on every date-ordered list.

Lists order by business date, so without an index each open sorts the
whole table (fine at 1k rows, not at 50k). Every date-ordered table
carries an index on its date column: declared on the model for fresh
databases and backfilled by migration for existing ones.

Run: python -m pytest backend/tests/test_date_indexes.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.app.models  # noqa: E402,F401  (registers every table on Base)
from backend.app.core.database import Base  # noqa: E402

# (table, date column) pairs behind the date-ordered list endpoints.
DATE_INDEXED = [
    ("sales_challans", "challan_date"),
    ("sales_invoices", "invoice_date"),
    ("purchase_invoices", "invoice_date"),
    ("expenses", "date"),
    ("production_entries", "production_date"),
    ("customer_payments", "payment_date"),
    ("supplier_payments", "payment_date"),
    ("audit_logs", "timestamp"),
]


def test_models_declare_date_indexes():
    missing = [
        f"{table}.{column}"
        for table, column in DATE_INDEXED
        if not any(
            column in {c.name for c in idx.columns}
            for idx in Base.metadata.tables[table].indexes
        )
    ]
    assert not missing, f"date columns with no index: {missing}"


def test_migration_backfills_date_indexes(temp_dir):
    import sqlite3

    from backend.app.core import database as dbmod

    path = os.path.join(temp_dir, "old.db")
    con = sqlite3.connect(path)
    try:
        con.execute(
            "CREATE TABLE expenses (id INTEGER PRIMARY KEY, date DATETIME)"
        )
        con.commit()
    finally:
        con.close()
    import sqlalchemy

    eng = sqlalchemy.create_engine(f"sqlite:///{path}")
    try:
        with eng.begin() as conn:
            dbmod._ensure_date_indexes(conn)
            dbmod._ensure_date_indexes(conn)  # idempotent
        with eng.connect() as conn:
            names = {
                r[0]
                for r in conn.execute(
                    sqlalchemy.text("select name from sqlite_master where type='index'")
                ).fetchall()
            }
    finally:
        eng.dispose()
    assert "ix_expenses_date" in names, sorted(names)


def test_date_order_avoids_temp_sort(db):
    from sqlalchemy import text as _text

    for table, column in DATE_INDEXED:
        plan = "\n".join(
            r[3]
            for r in db.execute(
                _text(f"EXPLAIN QUERY PLAN SELECT * FROM {table} ORDER BY {column} DESC, id DESC")
            ).fetchall()
            if len(r) > 3
        )
        assert "TEMP B-TREE" not in plan.upper(), (
            f"{table} sorts {column} without an index:\n{plan}"
        )
