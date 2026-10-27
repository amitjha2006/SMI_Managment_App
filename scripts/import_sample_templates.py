#!/usr/bin/env python
"""Import all sample Excel templates into a target database.

Usage:
    python scripts/import_sample_templates.py --db PATH_TO_DB

Example:
    python scripts/import_sample_templates.py --db C:\Shresth_Masala_Application_New\dist\ShresthMasalaIndustry\data\masala_factory.db
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Set DATABASE_URL BEFORE importing backend modules
# so the engine is created with the correct path
for i, arg in enumerate(sys.argv):
    if arg == "--db" and i + 1 < len(sys.argv):
        os.environ["DATABASE_URL"] = f"sqlite:///{sys.argv[i + 1]}"
        break

sys.path.insert(0, ROOT)

from io import BytesIO
from backend.app.core.database import SessionLocal
from backend.app.services.excel_import import (
    import_customers,
    import_suppliers,
    import_raw_materials,
    import_products,
    import_purchase_invoices,
    import_sales_challans,
    import_customer_payments,
    import_expenses,
    import_employees,
    read_excel_file,
)

TEMPLATE_DIR = os.path.join(ROOT, "templates")

IMPORT_ORDER = [
    ("customers", "sample_customers.xlsx", import_customers),
    ("suppliers", "sample_suppliers.xlsx", import_suppliers),
    ("raw_materials", "sample_raw_materials.xlsx", import_raw_materials),
    ("products", "sample_products.xlsx", import_products),
    ("purchase_invoices", "sample_purchase_invoices.xlsx", import_purchase_invoices),
    ("sales_challans", "sample_sales_challans.xlsx", import_sales_challans),
    ("customer_payments", "sample_customer_payments.xlsx", import_customer_payments),
    ("expenses", "sample_expenses.xlsx", import_expenses),
    ("employees", "sample_employees.xlsx", import_employees),
]


def read_file_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def main():
    ap = argparse.ArgumentParser(description="Import sample templates in dependency order.")
    ap.add_argument("--db", required=True, help="Path to target SQLite database")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"Database not found: {args.db}")
        return 1

    db = SessionLocal()
    try:
        for import_type, filename, fn in IMPORT_ORDER:
            path = os.path.join(TEMPLATE_DIR, filename)
            if not os.path.exists(path):
                print(f"  SKIP {import_type}: template not found at {path}")
                continue

            print(f"Importing {import_type} from {filename}...")
            content = read_file_bytes(path)
            _, data_rows = read_excel_file(content)
            result = fn(db, data_rows, import_type)
            summary = result.summary()
            print(f"  {import_type}: imported={summary['imported']}, skipped={summary['skipped']}, failed={summary['failed']}")
            if summary['errors']:
                for err in summary['errors'][:3]:
                    print(f"    ERROR row {err['row']}: {err['message']}")
            if summary['skipped_rows']:
                for sk in summary['skipped_rows'][:3]:
                    print(f"    SKIP row {sk['row']}: {sk['reason']}")
    finally:
        db.close()

    print("\nDone. Open the app and check Reports > Raw Material Summary etc.")
    return 0


if __name__ == "__main__":
    sys.exit(main())