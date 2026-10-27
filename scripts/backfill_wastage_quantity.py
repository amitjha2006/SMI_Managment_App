"""Backfill production_stage_entries.wastage_quantity.

Why this is needed
------------------
Production entries recorded a stage's wastage PERCENTAGE but never its
QUANTITY, so wastage_quantity kept its column default of 0 on every row. The
percentage is a rate without its base, and every wastage total in the system
read zero.

The quantity is not a new fact to be invented - it is fully determined by the
input and output already recorded on each stage row:

    wastage_kg = max(0, input_quantity - output_quantity)

So this derives what should have been stored at the time.

Safety
------
- Read-only by default. Pass --apply to actually write.
- Only touches rows where wastage_quantity is 0 or NULL, so it can never
  overwrite a real figure and re-running is harmless.
- Rows where output exceeds input are left at 0 and counted separately: that
  is a data mistake in the source row, not negative wastage.
- Takes its own timestamped backup before writing anything.
"""

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend.app.services.production_wastage import stage_loss  # noqa: E402

DB = os.path.join(ROOT, "data", "masala_factory.db")
BACKUP_DIR = os.path.join(ROOT, "data", "backups")


def preview(conn):
    """Report what would change, without writing."""
    rows = conn.execute(
        "select id, input_quantity, output_quantity, wastage_quantity "
        "from production_stage_entries "
        "where coalesce(wastage_quantity, 0) = 0 "
        "and coalesce(input_quantity, 0) > 0"
    ).fetchall()

    total = 0.0
    backwards = []
    for rid, in_q, out_q, _ in rows:
        loss = stage_loss(in_q, out_q)
        total += float(loss.wastage_kg)
        if float(loss.wastage_kg) == 0.0 and float(out_q or 0) > float(in_q or 0):
            backwards.append(rid)

    grand = conn.execute(
        "select count(*), round(coalesce(sum(input_quantity), 0), 3) from production_stage_entries"
    ).fetchone()
    already = conn.execute(
        "select count(*) from production_stage_entries where coalesce(wastage_quantity, 0) > 0"
    ).fetchone()[0]

    print(f"  stage rows total        : {grand[0]}")
    print(f"  already have wastage    : {already}")
    print(f"  rows to backfill        : {len(rows)}")
    print(f"  total loss to be stored : {total:.3f} kg")
    print(f"  total stage input       : {grand[1]} kg")
    if grand[1]:
        print(f"  resulting loss rate     : {total / float(grand[1]) * 100:.2f}% of input")
    if backwards:
        print(
            f"  WARNING: {len(backwards)} row(s) report more output than "
            f"input and stay 0: {backwards[:12]}"
        )
    return rows, total


def apply(conn, rows):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dest = os.path.join(BACKUP_DIR, f"pre-wastage-backfill_{stamp}.db")
    conn.commit()  # make sure the backup captures a settled database
    shutil.copy2(DB, dest)
    print(f"  backup written          : {dest}")

    changed = 0
    for rid, in_q, out_q, _ in rows:
        loss = stage_loss(in_q, out_q)
        if float(loss.wastage_kg) == 0.0:
            continue
        conn.execute(
            "update production_stage_entries "
            "set wastage_quantity = ?, wastage_percentage = ? where id = ?",
            (str(loss.wastage_kg), str(loss.wastage_pct), rid),
        )
        changed += 1
    conn.commit()
    print(f"  rows updated            : {changed}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the changes (default is a dry run)")
    args = ap.parse_args()

    if not os.path.exists(DB):
        print(f"database not found: {DB}")
        return 1

    conn = sqlite3.connect(DB)
    try:
        print("=== wastage backfill ===")
        rows, total = preview(conn)
        if not args.apply:
            print("\n  dry run - nothing written. Re-run with --apply to store these.")
            return 0
        if not rows:
            print("\n  nothing to do.")
            return 0
        print()
        apply(conn, rows)

        print("\n=== after ===")
        check = conn.execute(
            "select count(*), round(coalesce(sum(wastage_quantity), 0), 3), "
            "round(coalesce(sum(input_quantity), 0), 3) "
            "from production_stage_entries"
        ).fetchone()
        print(f"  stage rows      : {check[0]}")
        print(f"  wastage stored  : {check[1]} kg")
        print(f"  stage input     : {check[2]} kg")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
