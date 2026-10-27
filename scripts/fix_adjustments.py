"""Repair bad stock adjustment entries on the deployment DB.

Dry-run by default. Use --apply to actually modify the live database.
--apply refuses without --backup.

Corrections (UPDATE qty to the intended value):
  id=2558 Garam Masala (7g): -1.75 -> -2.5 kg  [note: bad (-2.5 kg)]
  id=2557 Garam Masala (7g): +175 -> +250 kg   [note: bad (+250 kg)]
  id=2556 Garam Masala (7g): -175 -> -250 kg   [note: wrong (-250 kg)]

Deletions (wrong entries - remove entirely):
  id=22 Black Pepper (50g): -2 kg "Wrong Entry"
  id=21 Black Pepper (50g): +1 kg "Wrong entry"
  id=20 Black Pepper (100g): -62.15 kg "Wrong entry"
  id=2 Coriander Powder (250g): -17588 kg "Wrong entry"
"""
import os
import shutil
import sys
import tempfile
import argparse
from datetime import datetime
from sqlalchemy import create_engine, text

REAL = r"C:\Shresth_Masala_Application_New\ShresthMasala-Laptop\data\masala_factory.db"

# (id, new_qty_or_None, action)  action: "correct" (update qty) or "delete"
FIXES = [
    (2558, -2.5, "correct"),
    (2557, 250.0, "correct"),
    (2556, -250.0, "correct"),
    (22, None, "delete"),
    (21, None, "delete"),
    (20, None, "delete"),
    (2, None, "delete"),
]


def apply_fixes(engine, label):
    """Apply all fixes to the given engine. Returns list of what was done."""
    done = []
    with engine.begin() as conn:
        for fid, new_qty, action in FIXES:
            # Verify entry still exists before touching
            row = conn.execute(text("""
                select id, quantity, notes from finished_product_stock_movements where id = :id
            """), {"id": fid}).fetchone()
            if not row:
                done.append(f"  id={fid} NOT FOUND (skipped)")
                continue
            if action == "correct":
                conn.execute(text("""
                    update finished_product_stock_movements
                    set quantity = :qty
                    where id = :id
                """), {"qty": new_qty, "id": fid})
                done.append(f"  Updated id={fid} quantity {row[1]} -> {new_qty} ({row[2]!r})")
            elif action == "delete":
                conn.execute(text("""
                    delete from finished_product_stock_movements where id = :id
                """), {"id": fid})
                done.append(f"  Deleted id={fid} (was qty={row[1]}, {row[2]!r})")
    return done


def main():
    apply = False
    backup = False
    for a in sys.argv[1:]:
        if a == "--apply":
            apply = True
        elif a == "--backup":
            backup = True

    if apply and not backup:
        print("ERROR: --apply requires --backup. Refusing to modify without backup.")
        sys.exit(1)

    tmp = tempfile.mkdtemp(prefix="sminvest_repair_")
    copy = os.path.join(tmp, "copy.db")
    shutil.copy2(REAL, copy)
    print(f"Working on copy: {copy}")

    # Show current state on the copy
    print("\n=== Current state of entries to fix ===")
    cengine = create_engine("sqlite:///" + copy)
    with cengine.connect() as conn:
        for fid, _, action in FIXES:
            row = conn.execute(text("""
                select id, quantity, notes, created_at, product_id, packaging_size_id
                from finished_product_stock_movements where id = :id
            """), {"id": fid}).fetchone()
            if row:
                print(f"  id={row[0]} qty={row[1]} notes={row[2]!r} created={row[3]} prod_id={row[4]} size_id={row[5]} -> {action}")
            else:
                print(f"  id={fid} NOT FOUND")

    if not apply:
        print("\n[DRY-RUN] No changes made. Re-run with --apply --backup to modify the live DB.")
        cengine.dispose()
        return

    # Backup the live DB
    backup_path = REAL + f".fix_adjustments_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(REAL, backup_path)
    print(f"\nBacked up live DB to {backup_path}")

    # Apply fixes to the LIVE DB
    real_engine = create_engine("sqlite:///" + REAL)
    print("\n=== Applying fixes to LIVE DB ===")
    done = apply_fixes(real_engine, "live")
    for d in done:
        print(d)
    real_engine.dispose()
    cengine.dispose()
    print("\nApplied all fixes to live DB.")


if __name__ == "__main__":
    main()
