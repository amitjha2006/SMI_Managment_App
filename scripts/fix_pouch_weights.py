"""Normalise every pack size's empty-pouch weight to the agreed table.

The pouch weight feeds two things at production time: the wrapper quantity
(packets x pouch weight) and the packing gain a batch reports. Sizes created
before this table changed mostly read 0 (wrapper accounting off) and a few
carry older numbers, so the same product could deduct 3g for a 50g pouch on
one size and 0 on another.

Agreed table (grams):
    7g -> 2, 50g -> 4, 100g -> 6, 175g -> 6, 200g -> 8,
    250g -> 8, 400g -> 10, 500g -> 10, Loose Powder -> 14
A 1kg size keeps the 17g default it already had (the agreed list stops at
500g, so that row is unchanged in spirit).

Safety rules (see AGENTS.md):
- Refuses the repo's live database path outright.
- Refuses to write unless --apply is given (default is a dry-run report).
- Refuses --apply without a backup (pass --backup to make one).
- Idempotent: a size already at its target weight is reported and skipped.
- Unlisted sizes are left exactly as they are, never zeroed.

Run (from the repo root, laptop app CLOSED):
    python scripts/fix_pouch_weights.py --db <path>                    # dry run
    python scripts/fix_pouch_weights.py --db <path> --apply --backup
"""

import argparse
import contextlib
import os
import shutil
import sys
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from backend.app import models  # noqa: E402

REPO_LIVE_DB = os.path.join(ROOT, "data", "masala_factory.db")

# Mirrors _DEFAULT_POUCH_KG in backend/main.py. Kept as a copy on purpose:
# importing backend.main runs init_db() against the repo database, and a
# repair script must not touch anything but the --db it was pointed at.
# tests/test_pouch_weight_default.py pins the two copies together.
POUCH_G_BY_SIZE = {
    7: 2, 50: 4, 100: 6, 175: 6, 200: 8, 250: 8, 400: 10, 500: 10,
    # Unchanged legacy entry: the agreed list stopped at 500g, and a 1kg
    # pouch already defaulted to 17g. Kept so the script and the backend
    # default agree rather than silently leaving 1kg rows alone.
    1000: 17,
}
LOOSE_POUCH_G = 14


def target_pouch_kg(size_name, weight_in_kg):
    """Target weight in kg, or None when the size is not in the table."""
    if "loose" in (size_name or "").casefold():
        return Decimal(str(LOOSE_POUCH_G)) / Decimal(1000)
    grams = POUCH_G_BY_SIZE.get(round(float(weight_in_kg or 0) * 1000))
    if grams is None:
        return None
    return Decimal(str(grams)) / Decimal(1000)


def validate(args, db_path):
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


def ensure_column(db):
    """The app adds wrapper_material_id on startup; a repair run may come
    first, and the model now selects it. Additive and nullable, so it cannot
    change any data."""
    cols = [row[1] for row in db.execute(text("PRAGMA table_info(packaging_sizes)"))]
    if "wrapper_material_id" not in cols:
        db.execute(text("ALTER TABLE packaging_sizes ADD COLUMN wrapper_material_id INTEGER"))
        db.commit()
        print("note: added packaging_sizes.wrapper_material_id (the app does this on startup)")


def collect(db):
    """Rows to change, rows already correct, rows this table does not cover."""
    todo, ok, unknown = [], 0, []
    sizes = db.query(models.PackagingSize).order_by(models.PackagingSize.id).all()
    for size in sizes:
        target = target_pouch_kg(size.size_name, size.weight_in_kg)
        if target is None:
            unknown.append(size)
            continue
        current = Decimal(str(size.wrapper_weight_kg or 0))
        product = db.get(models.Product, size.product_id)
        label = f"{size.id:>4}  {(product.name if product else '?'):<28} {size.size_name:<14}"
        if current == target:
            ok += 1
            continue
        todo.append((size, current, target, label))
    return todo, ok, unknown


def report(todo, ok, unknown, db):
    print(f"{'id':>4}  {'product':<28} {'size':<14} {'now(g)':>7} -> {'target(g)':>8}")
    print("-" * 78)
    for _size, current, target, label in todo:
        print(f"{label} {current * 1000:>7.1f} -> {target * 1000:>8.1f}")
    print("-" * 78)
    print(f"rows to change : {len(todo)}")
    print(f"already correct: {ok}")
    if unknown:
        print(f"not in the table: {len(unknown)} (left untouched)")
        for size in unknown:
            product = db.get(models.Product, size.product_id)
            print(
                f"    {size.id:>4}  {(product.name if product else '?'):<28} "
                f"{size.size_name}"
            )


def apply_changes(db, todo):
    for size, _current, target, _label in todo:
        size.wrapper_weight_kg = target
    db.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Path to the database file to repair.")
    parser.add_argument("--apply", action="store_true",
                        help="Write the changes. Without this the script only reports.")
    parser.add_argument("--backup", action="store_true",
                        help="Copy the database to <db>.fix_pouch_weights_bak before writing.")
    args = parser.parse_args()

    # Product names contain Devanagari; the default cp1252 console cannot
    # encode them, which must not abort a repair script mid-report.
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
        ensure_column(db)
        todo, ok, unknown = collect(db)
        report(todo, ok, unknown, db)
        if not todo:
            print("nothing to do.")
            return 0
        if not args.apply:
            print("dry run - nothing written. Re-run with --apply (and --backup) to write.")
            return 0
        if args.backup:
            bak = f"{db_path}.fix_pouch_weights_bak"
            shutil.copy2(db_path, bak)
            print(f"backup written: {bak}")
        apply_changes(db, todo)
        print(f"written: {len(todo)} pack sizes updated.")
        return 0
    finally:
        db.close()
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
