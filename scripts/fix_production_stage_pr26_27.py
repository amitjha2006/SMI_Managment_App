"""Repair broken stage rows on production entry PR/26-27/0001.

The entry's outputs are correct (1000x50g + 100x100g + 150x250g = 97.5 kg),
but its stage_entries were recorded wrong:

  Stage 1 GRINDING:   in 100 -> out 98.001     (ok)
  Stage 2 PACKAGING:  in 98.001 -> out 0       (WRONG: out 0, loss 100%)
  Stage 3 PACKAGING:  in 0 -> out 5.8          (WRONG: duplicate, in 0)

Correction:
  Stage 2 PACKAGING:  in 98.001 -> out 97.5    (bulk powder in, packed out)
                      wastage 0.501 kg (~0.51%), packing gain 0
  Stage 3 PACKAGING:  deleted (duplicate nonsense row)

Safety rules (see AGENTS.md):
- Refuses the repo's live data/masala_factory.db outright.
- Refuses to write unless --apply is given (default is a dry-run report).
- Refuses --apply without --backup.
- Idempotent: already-corrected rows are skipped.

Run (from the repo root, laptop app CLOSED):
    python scripts/fix_production_stage_pr26_27.py --db <path>            # dry run
    python scripts/fix_production_stage_pr26_27.py --db <path> --apply --backup
"""

import argparse
import contextlib
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from decimal import ROUND_HALF_UP, Decimal  # noqa: E402

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from backend.app import models  # noqa: E402

REPO_LIVE_DB = os.path.join(ROOT, "data", "masala_factory.db")
ENTRY_NUMBER = "PR/26-27/0001"

D2 = Decimal("0.01")
D3 = Decimal("0.001")


def q2(v) -> Decimal:
    return Decimal(str(v)).quantize(D2, rounding=ROUND_HALF_UP)


def q3(v) -> Decimal:
    return Decimal(str(v)).quantize(D3, rounding=ROUND_HALF_UP)


def validate(args, db_path: str) -> list[str]:
    errors = []
    if not os.path.exists(db_path):
        errors.append(f"database not found: {db_path}")
    if os.path.abspath(REPO_LIVE_DB) == os.path.abspath(db_path):
        errors.append(
            "refusing the repo's live data/masala_factory.db - run this "
            "against the laptop database (or a copy of it)."
        )
    if args.apply and not args.backup:
        errors.append(
            "--apply without --backup is refused; copy the database yourself "
            "or pass --backup to have the script do it."
        )
    return errors


def find_entry(db: Session):
    return (
        db.query(models.ProductionEntry)
        .filter(models.ProductionEntry.production_number == ENTRY_NUMBER)
        .first()
    )


def print_report(db: Session, entry):
    stages = (
        db.query(models.ProductionStageEntry)
        .filter(models.ProductionStageEntry.production_entry_id == entry.id)
        .order_by(models.ProductionStageEntry.sequence)
        .all()
    )
    print(f"entry {entry.id} '{entry.production_number}' ({entry.status})")
    print(f"  actual_output stored: {entry.actual_output} kg")
    print(f"  stage_entries: {len(stages)}")
    for s in stages:
        print(
            f"  stage {s.sequence} {s.stage}: "
            f"input={s.input_quantity} output={s.output_quantity} "
            f"wastage={s.wastage_quantity} loss%={s.wastage_percentage} "
            f"gain={s.packing_gain_kg}"
        )
    # Show what correction would be.
    gr = stages[0] if stages else None
    pk2 = stages[1] if len(stages) > 1 else None
    pk3 = stages[2] if len(stages) > 2 else None
    bulk_in = Decimal(str(gr.output_quantity)) if gr else Decimal(0)
    actual_out = Decimal(str(entry.actual_output or 0))
    wastage = q3(bulk_in - actual_out)
    loss_pct = q2((wastage / bulk_in * 100) if bulk_in else 0)
    print("  CORRECTION (dry run):")
    print(f"    stage 2 PACKAGING: input={bulk_in} output={actual_out} "
          f"wastage={wastage} loss%={loss_pct} gain=0")
    if pk3:
        print(f"    DELETE stage {pk3.sequence} PACKAGING "
              f"(input={pk3.input_quantity} output={pk3.output_quantity})")
    return bulk_in, actual_out, wastage, loss_pct, pk2, pk3


def apply_fix(db_path: str, entry_id: int, bulk_in: Decimal, actual_out: Decimal,
              wastage: Decimal, loss_pct: Decimal, pk3_id: int) -> int:
    engine = create_engine(f"sqlite:///{db_path}")
    db = sessionmaker(bind=engine)()
    try:
        entry = db.get(models.ProductionEntry, entry_id)
        pk2 = (
            db.query(models.ProductionStageEntry)
            .filter(
                models.ProductionStageEntry.production_entry_id == entry_id,
                models.ProductionStageEntry.stage == "PACKAGING",
                models.ProductionStageEntry.sequence == 2,
            )
            .first()
        )
        if pk2:
            pk2.input_quantity = float(bulk_in)
            pk2.output_quantity = float(actual_out)
            pk2.wastage_quantity = float(wastage)
            pk2.wastage_percentage = float(loss_pct)
            pk2.packing_gain_kg = 0.0
            print(f"  wrote stage 2 PACKAGING: in={bulk_in} out={actual_out} "
                  f"wastage={wastage} loss%={loss_pct} gain=0")
        # Delete the duplicate nonsense stage 3 row.
        pk3 = db.get(models.ProductionStageEntry, pk3_id)
        if pk3 and pk3.production_entry_id == entry_id and pk3.sequence == 3:
            db.delete(pk3)
            print(f"  deleted stage {pk3.sequence} PACKAGING "
                  f"(was in={pk3.input_quantity} out={pk3.output_quantity})")
        db.commit()
        return 0
    finally:
        db.close()
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Path to the database file to repair.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the fixes. Without this the script only reports.",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="Copy the database to <db>.fix_production_stage_bak before writing.",
    )
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError):
            stream.reconfigure(encoding="utf-8", errors="replace")

    db_path = os.path.abspath(args.db)
    errors = validate(args, db_path)
    if errors:
        for err in errors:
            print(f"ERROR: {err}")
        return 2

    engine = create_engine(f"sqlite:///{db_path}")
    db = sessionmaker(bind=engine)()
    try:
        entry = find_entry(db)
        if entry is None:
            print(f"ERROR: entry '{ENTRY_NUMBER}' not found in {db_path}")
            return 2
        bulk_in, actual_out, wastage, loss_pct, pk2, pk3 = print_report(db, entry)
        if not args.apply:
            print("dry run - nothing written. Re-run with --apply (and --backup) to write.")
            return 0
        pk3_id = pk3.id if pk3 else None
    finally:
        db.close()
        engine.dispose()

    if args.backup:
        bak = f"{db_path}.fix_production_stage_bak"
        shutil.copy2(db_path, bak)
        print(f"backup written: {bak}")
    return apply_fix(db_path, entry.id, bulk_in, actual_out, wastage, loss_pct, pk3_id)


if __name__ == "__main__":
    sys.exit(main())
