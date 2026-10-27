"""Demo data for the Raw Material Summary report. Seed it, look at the
application, remove it when done.

Every row this creates is marked DEMO (codes, names, numbers, notes), so
--clean can find exactly what --seed added and nothing else.

Safety (repo rule: throwaway scripts target a COPY, never the live DB)
----------------------------------------------------------------------
- Default database is a --db PATH you pass. Point it at a COPY of
  data/masala_factory.db to try things safely.
- To seed the live database, pass --live explicitly. A timestamped
  backup is taken first, and --seed refuses when DEMO rows exist.
- --clean deletes in FK-safe order (children before parents) and
  reverses the stock/outstanding edits --seed made, then verifies zero
  DEMO rows remain.

Usage
-----
python scripts/seed_rm_demo.py --db C:\\tmp\\copy.db --seed
python scripts/seed_rm_demo.py --db C:\\tmp\\copy.db --clean
python scripts/seed_rm_demo.py --live --seed    # live DB, backup first
python scripts/seed_rm_demo.py --live --clean
"""

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE_DB = os.path.join(ROOT, "data", "masala_factory.db")
BACKUP_DIR = os.path.join(ROOT, "data", "backups")

IST = timezone(timedelta(hours=5, minutes=30))


def _now():
    """Naive IST wall time, the same convention the backend stores."""
    return datetime.now(IST).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _connect(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _demo_counts(conn):
    tables = {
        "raw_materials": "material_id LIKE 'RM-DEMO-%'",
        "purchase_invoices": "invoice_number LIKE 'DEMO-%'",
        "production_entries": "production_number LIKE 'DEMO-%'",
        "products": "product_id = 'P-DEMO'",
        "suppliers": "supplier_id = 'SUP-DEMO'",
    }
    return {t: conn.execute(f"select count(*) from {t} where {w}").fetchone()[0]
            for t, w in tables.items()}


def seed(conn):
    existing = _demo_counts(conn)
    if any(existing.values()):
        print("DEMO rows already present, refusing to seed twice:", existing)
        print("Run --clean first.")
        return False
    now = _now()

    sup = conn.execute(
        "insert into suppliers (supplier_id, name, contact_number, is_active, created_at)"
        " values ('SUP-DEMO', 'DEMO Supplier (delete me)', '9000000001', 1, ?)",
        (now,),
    ).lastrowid

    mats = {}
    for code, name, opening in [
        ("RM-DEMO-TUR", "DEMO Turmeric (\u0939\u0932\u094d\u0926\u0940)", 100),
        ("RM-DEMO-COR", "DEMO Coriander (\u0927\u0928\u093f\u092f\u093e)", 50),
        ("RM-DEMO-IDLE", "DEMO Idle Seed", 0),
    ]:
        mats[code] = conn.execute(
            "insert into raw_materials (material_id, name, unit, opening_stock,"
            " current_stock, purchase_rate, is_active, created_at)"
            # Unit is an ORM enum: the stored form is the NAME ('KG'), and a
            # lowercase value breaks every ORM read of the table.
            " values (?, ?, 'KG', ?, ?, 100, 1, ?)",
            (code, name, opening, opening, now),
        ).lastrowid

    purchases = [
        ("DEMO-PUR-1", "2026-09-05 10:00:00", "RM-DEMO-TUR", 500, 120),
        ("DEMO-PUR-2", "2026-09-06 10:00:00", "RM-DEMO-COR", 200, 90),
    ]
    outstanding = 0.0
    for number, day, code, qty, rate in purchases:
        inv = conn.execute(
            "insert into purchase_invoices (invoice_number, invoice_date, supplier_id,"
            " total_amount, paid_amount, outstanding_amount, is_confirmed,"
            " created_at, is_archived) values (?, ?, ?, ?, 0, ?, 1, ?, 0)",
            (number, day, sup, qty * rate, qty * rate, now),
        ).lastrowid
        conn.execute(
            "insert into purchase_invoice_items (purchase_invoice_id, raw_material_id,"
            " quantity, unit, rate, amount, created_at)"
            " values (?, ?, ?, 'kg', ?, ?, ?)",
            (inv, mats[code], qty, rate, qty * rate, now),
        )
        conn.execute(
            "update raw_materials set current_stock = current_stock + ? where id = ?",
            (qty, mats[code]),
        )
        conn.execute(
            "insert into raw_material_stock_movements (raw_material_id, movement_type,"
            " reference_type, reference_id, quantity, unit, rate, notes, created_at)"
            " values (?, 'purchase', 'purchase_invoice', ?, ?, 'kg', ?, ?, ?)",
            (mats[code], inv, qty, rate, f"DEMO purchase {number}", now),
        )
        outstanding += qty * rate
    conn.execute(
        "update suppliers set outstanding_balance = coalesce(outstanding_balance, 0) + ?"
        " where id = ?",
        (outstanding, sup),
    )

    prod = conn.execute(
        "insert into products (product_id, name, is_active, created_at)"
        " values ('P-DEMO', 'DEMO Masala (delete me)', 1, ?)",
        (now,),
    ).lastrowid
    size = conn.execute(
        "insert into packaging_sizes (product_id, size_name, weight_in_kg,"
        " packets_per_unit, rate_per_kg, is_active, created_at)"
        " values (?, 'DEMO 250g', 0.25, 1, 200, 1, ?)",
        (prod, now),
    ).lastrowid

    batches = [
        ("DEMO-PROD-1", "2026-09-10 10:00:00",
         [("RM-DEMO-TUR", 300), ("RM-DEMO-COR", 100)],
         [("roasting", 400, 380), ("grinding", 380, 372)], 1488),
        ("DEMO-PROD-2", "2026-09-20 10:00:00",
         [("RM-DEMO-TUR", 120)], [("roasting", 120, 114)], 456),
    ]
    for number, day, consumed, stages, packets in batches:
        entry = conn.execute(
            "insert into production_entries (production_number, production_date,"
            " product_id, packaging_size_id, target_quantity, actual_output,"
            " status, notes, created_at)"
            # ORM enums store the NAME ('COMPLETED'); lowercase breaks ORM reads.
            " values (?, ?, ?, ?, 400, 0, 'COMPLETED', 'DEMO batch', ?)",
            (number, day, prod, size, now),
        ).lastrowid
        for i, (stage, in_q, out_q) in enumerate(stages, 1):
            conn.execute(
                "insert into production_stage_entries (production_entry_id, stage,"
                " sequence, input_quantity, output_quantity, wastage_quantity,"
                " wastage_percentage, packing_gain_kg, created_at)"
                # Stage enum stores the NAME ('ROASTING').
                " values (?, ?, ?, ?, ?, ?, ?, 0, ?)",
                (entry, stage.upper(), i, in_q, out_q, max(0, in_q - out_q),
                 round(max(0, in_q - out_q) / in_q * 100, 2) if in_q else 0, now),
            )
        out_kg = packets * 0.25
        conn.execute(
            "insert into production_outputs (production_entry_id, packaging_size_id,"
            " packets, quantity_kg, created_at) values (?, ?, ?, ?, ?)",
            (entry, size, packets, out_kg, now),
        )
        conn.execute(
            "update production_entries set actual_output = ? where id = ?",
            (out_kg, entry),
        )
        conn.execute(
            "insert into finished_product_stock_movements (product_id, packaging_size_id,"
            # Movement enum stores the NAME ('PRODUCTION').
            " movement_type, reference_type, reference_id, quantity, packets,"
            " rate_per_kg, notes, created_at)"
            " values (?, ?, 'PRODUCTION', 'production_entry', ?, ?, ?, 200, ?, ?)",
            (prod, size, entry, out_kg, packets, f"DEMO production {number}", now),
        )
        for code, qty in consumed:
            conn.execute(
                "update raw_materials set current_stock = current_stock - ? where id = ?",
                (qty, mats[code]),
            )
            conn.execute(
                "insert into raw_material_stock_movements (raw_material_id, movement_type,"
                " reference_type, reference_id, quantity, unit, rate, notes, created_at)"
                " values (?, 'consumption', 'production_entry', ?, ?, 'kg', 100, ?, ?)",
                (mats[code], entry, qty, f"DEMO production {number}", now),
            )
    conn.commit()
    print("Seeded:", _demo_counts(conn))
    print("Open Reports > Raw Material Summary, range 2026-09-01 to 2026-09-30.")
    return True


def clean(conn):
    mat_ids = [r[0] for r in conn.execute(
        "select id from raw_materials where material_id LIKE 'RM-DEMO-%'").fetchall()]
    entry_ids = [r[0] for r in conn.execute(
        "select id from production_entries where production_number LIKE 'DEMO-%'").fetchall()]
    inv_ids = [r[0] for r in conn.execute(
        "select id from purchase_invoices where invoice_number LIKE 'DEMO-%'").fetchall()]

    # Reverse the stock edits first, while the rows are still identifiable.
    for (mid, qty) in conn.execute(
        "select raw_material_id, quantity from raw_material_stock_movements"
        " where movement_type = 'purchase' and notes LIKE 'DEMO purchase %'").fetchall():
        conn.execute("update raw_materials set current_stock = current_stock - ?"
                     " where id = ?", (qty, mid))
    for (mid, qty) in conn.execute(
        "select raw_material_id, quantity from raw_material_stock_movements"
        " where movement_type = 'consumption' and notes LIKE 'DEMO production %'").fetchall():
        conn.execute("update raw_materials set current_stock = current_stock + ?"
                     " where id = ?", (qty, mid))
    total = conn.execute(
        "select coalesce(sum(total_amount), 0) from purchase_invoices"
        " where invoice_number LIKE 'DEMO-%'").fetchone()[0]
    conn.execute(
        "update suppliers set outstanding_balance = outstanding_balance - ?"
        " where supplier_id = 'SUP-DEMO'", (total,))

    # Children before parents (FK enforcement is ON).
    if mat_ids:
        q = ",".join("?" * len(mat_ids))
        conn.execute(f"delete from raw_material_stock_movements where raw_material_id in ({q})",
                     mat_ids)
    if entry_ids:
        q = ",".join("?" * len(entry_ids))
        for table, col in [("finished_product_stock_movements", "reference_id"),
                           ("production_outputs", "production_entry_id"),
                           ("production_stage_entries", "production_entry_id")]:
            if table == "finished_product_stock_movements":
                conn.execute(
                    f"delete from {table} where reference_type = 'production_entry'"
                    f" and {col} in ({q})", entry_ids)
            else:
                conn.execute(f"delete from {table} where {col} in ({q})", entry_ids)
        conn.execute(f"delete from production_entries where id in ({q})", entry_ids)
    if inv_ids:
        q = ",".join("?" * len(inv_ids))
        conn.execute(f"delete from purchase_invoice_items where purchase_invoice_id in ({q})",
                     inv_ids)
        conn.execute(f"delete from purchase_invoices where id in ({q})", inv_ids)
    conn.execute("delete from packaging_sizes where product_id in"
                 " (select id from products where product_id = 'P-DEMO')")
    conn.execute("delete from products where product_id = 'P-DEMO'")
    conn.execute("delete from raw_materials where material_id LIKE 'RM-DEMO-%'")
    conn.execute("delete from suppliers where supplier_id = 'SUP-DEMO'")
    conn.commit()

    remaining = _demo_counts(conn)
    leftovers = conn.execute(
        "select count(*) from raw_material_stock_movements"
        " where notes LIKE 'DEMO%'").fetchone()[0]
    leftovers += conn.execute(
        "select count(*) from finished_product_stock_movements"
        " where notes LIKE 'DEMO%'").fetchone()[0]
    print("Remaining:", remaining, "leftover movements:", leftovers)
    ok = not any(remaining.values()) and leftovers == 0
    print("Clean." if ok else "CLEAN INCOMPLETE - inspect before continuing.")
    return ok


def main():
    ap = argparse.ArgumentParser(description="Seed or remove DEMO raw-material data.")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--db", default=None, help="Database file (try a COPY first).")
    ap.add_argument("--live", action="store_true",
                    help="Target data/masala_factory.db (backup taken first).")
    args = ap.parse_args()
    if args.seed == args.clean:
        ap.error("pass exactly one of --seed or --clean")

    if args.db:
        path = args.db
    elif args.live:
        path = LIVE_DB
        os.makedirs(BACKUP_DIR, exist_ok=True)
        backup = os.path.join(
            BACKUP_DIR, "pre-demo-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".db")
        shutil.copy2(path, backup)
        print("Backup:", backup)
    else:
        ap.error("pass --db PATH (a copy, to try safely) or --live (backup first)")

    if not os.path.exists(path):
        ap.error(f"database not found: {path}")
    conn = _connect(path)
    try:
        ok = seed(conn) if args.seed else clean(conn)
    finally:
        conn.close()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
