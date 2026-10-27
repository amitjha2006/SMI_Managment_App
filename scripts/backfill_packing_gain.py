"""Backfill production_stage_entries.packing_gain_kg.

Why this is needed
------------------
Packaging-stage output can legitimately exceed its input: the wrapper adds
real weight. Before packing gain was tracked, those rows stored wastage 0
(the clamp) and the surplus vanished from every report. The gain is fully
determined by the input and output already recorded on each row:

    gain_kg = output_quantity - input_quantity   (packaging rows only,
                                                  where output > input)

So this derives what should have been stored at the time.

Safety
------
- Read-only by default. Pass --apply to actually write.
- Only touches PACKAGING rows where output exceeds input and packing_gain_kg
  is 0 or NULL, so it can never overwrite a real figure and re-running is
  harmless. Backwards rows on other stages stay 0 - outside packaging that
  is a data mistake, not a gain.
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


def _has_column(conn):
    return any(
        row[1] == "packing_gain_kg"
        for row in conn.execute("PRAGMA table_info(production_stage_entries)").fetchall()
    )


def preview(conn):
    """Report what would change, without writing."""
    rows = conn.execute(
        "select id, input_quantity, output_quantity "
        "from production_stage_entries "
        "where lower(stage) = 'packaging' "
        "and coalesce(output_quantity, 0) > coalesce(input_quantity, 0) "
        "and coalesce(packing_gain_kg, 0) = 0"
    ).fetchall()

    total = 0.0
    for _rid, in_q, out_q in rows:
        total += float(stage_loss(in_q, out_q, is_packaging=True).packing_gain_kg)

    grand = conn.execute(
        "select count(*) from production_stage_entries where lower(stage) = 'packaging'"
    ).fetchone()[0]
    mistakes = conn.execute(
        "select count(*) from production_stage_entries "
        "where lower(stage) != 'packaging' "
        "and coalesce(output_quantity, 0) > coalesce(input_quantity, 0)"
    ).fetchone()[0]

    print(f"  packaging rows total      : {grand}")
    print(f"  rows to backfill gain    : {len(rows)}")
    print(f"  total gain to be stored  : {total:.3f} kg")
    if mistakes:
        print(
            f"  NOTE: {mistakes} non-packaging row(s) report more output "
            f"than input and stay 0 (likely data mistakes)"
        )
    return rows, total


def apply(conn, rows):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dest = os.path.join(BACKUP_DIR, f"pre-packing-gain-backfill_{stamp}.db")
    conn.commit()  # make sure the backup captures a settled database
    shutil.copy2(DB, dest)
    print(f"  backup written            : {dest}")

    changed = 0
    for rid, in_q, out_q in rows:
        gain = stage_loss(in_q, out_q, is_packaging=True).packing_gain_kg
        if float(gain) == 0.0:
            continue
        conn.execute(
            "update production_stage_entries set packing_gain_kg = ? where id = ?",
            (str(gain), rid),
        )
        changed += 1
    conn.commit()
    print(f"  rows updated              : {changed}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the changes (default is a dry run)")
    args = ap.parse_args()

    if not os.path.exists(DB):
        print(f"database not found: {DB}")
        return 1

    conn = sqlite3.connect(DB)
    try:
        if not _has_column(conn):
            print(
                "packing_gain_kg column not found - start the app once so the "
                "automatic migration adds it, then re-run this script."
            )
            return 1
        print("=== packing gain backfill ===")
        rows, _total = preview(conn)
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
            "select count(*), round(coalesce(sum(packing_gain_kg), 0), 3) "
            "from production_stage_entries"
        ).fetchone()
        print(f"  stage rows      : {check[0]}")
        print(f"  gain stored     : {check[1]} kg")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
