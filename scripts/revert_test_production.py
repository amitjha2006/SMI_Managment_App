"""Revert the test production entry PROD-8922-1792670199 and restore stock.

Removes the test batch and its stock postings, then puts the consumed raw
material (Turmeric 50 kg) and wrapper roll (Pouch Turmeric 250g 1.6 kg)
back to their pre-production levels.

Usage (dry-run, safe):
    python scripts/revert_test_production.py --db <copy>

Usage (apply, requires --backup):
    python scripts/revert_test_production.py --db <copy> --apply --backup

Never targets the live data/masala_factory.db path.
"""

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

LIVE_DB = Path("data/masala_factory.db")
TARGET_NUMBER = "PROD-8922-1792670199"

# Raw material restorations: id -> kg to add back
RESTORE_STOCK = {
    1: 50.0,  # Turmeric (consumed at save)
    45: 1.6,  # Pouch Turmeric 250g (consumed as wrapper at pack)
}


def backup(db_path: Path) -> Path:
    bak = db_path.with_suffix(db_path.suffix + ".revert_test_bak")
    shutil.copy2(db_path, bak)
    return bak


def main() -> int:
    ap = argparse.ArgumentParser(description="Revert test production entry")
    ap.add_argument("--db", required=True, help="Path to a copy of the DB (never live)")
    ap.add_argument("--apply", action="store_true", help="Actually apply changes")
    ap.add_argument("--backup", action="store_true", help="Create backup before applying")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}")
        return 1

    # Refuse live path
    if db_path.resolve() == LIVE_DB.resolve():
        print("ERROR: refusing to target live data/masala_factory.db")
        print("  Copy it first, then run against the copy.")
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    c = conn.cursor()

    # Find the entry
    c.execute(
        "SELECT id, production_number, status, packed_at, actual_output "
        "FROM production_entries WHERE production_number = ?",
        (TARGET_NUMBER,),
    )
    row = c.fetchone()
    if not row:
        print(f"No production entry found with number {TARGET_NUMBER}")
        conn.close()
        return 0

    eid = row[0]
    prod_number = row[1]
    print(
        f"Found entry id={eid} number={prod_number} status={row[2]} "
        f"packed_at={row[3]} actual_output={row[4]}"
    )

    # Count related rows (dry-run info)
    c.execute("SELECT COUNT(*) FROM production_stage_entries WHERE production_entry_id = ?", (eid,))
    stages = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM production_outputs WHERE production_entry_id = ?", (eid,))
    outputs = c.fetchone()[0]
    c.execute(
        "SELECT COUNT(*) FROM finished_product_stock_movements "
        "WHERE reference_id = ? AND movement_type = 'PRODUCTION'",
        (eid,),
    )
    fin_movements = c.fetchone()[0]
    c.execute(
        "SELECT COUNT(*) FROM raw_material_stock_movements WHERE reference_id = ?",
        (eid,),
    )
    raw_movements = c.fetchone()[0]

    print("\nWould delete:")
    print(f"  production_stage_entries: {stages} rows")
    print(f"  production_outputs: {outputs} rows")
    print(f"  finished_product_stock_movements (PRODUCTION): {fin_movements} rows")
    print(f"  raw_material_stock_movements: {raw_movements} rows")
    print("  production_entries: 1 row")
    print("\nWould restore stock:")
    for mid, kg in RESTORE_STOCK.items():
        c.execute("SELECT name, current_stock FROM raw_materials WHERE id = ?", (mid,))
        mrow = c.fetchone()
        if mrow:
            name = mrow[0].encode("ascii", "replace").decode("ascii")
            print(f"  {name} (id={mid}): {mrow[1]} + {kg} = {mrow[1] + kg}")
        else:
            print(f"  id={mid}: not found")

    if not args.apply:
        print("\nDry-run only. Use --apply --backup to make changes.")
        conn.close()
        return 0

    if not args.backup:
        print("\n--apply requires --backup. Aborting.")
        conn.close()
        return 1

    bak = backup(db_path)
    print(f"\nBackup created: {bak}")

    # Delete in dependency order
    c.execute("DELETE FROM production_outputs WHERE production_entry_id = ?", (eid,))
    c.execute("DELETE FROM production_stage_entries WHERE production_entry_id = ?", (eid,))
    c.execute(
        "DELETE FROM finished_product_stock_movements "
        "WHERE reference_id = ? AND movement_type = 'PRODUCTION'",
        (eid,),
    )
    c.execute(
        "DELETE FROM raw_material_stock_movements WHERE reference_id = ?",
        (eid,),
    )
    c.execute("DELETE FROM production_entries WHERE id = ?", (eid,))

    # Restore stock
    for mid, kg in RESTORE_STOCK.items():
        c.execute(
            "UPDATE raw_materials SET current_stock = current_stock + ? WHERE id = ?",
            (kg, mid),
        )

    conn.commit()
    conn.close()
    print("\nApplied successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
