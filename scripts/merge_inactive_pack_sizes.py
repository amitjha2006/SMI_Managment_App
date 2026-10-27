"""Merge deactivated duplicate packaging sizes into their active twin.

A stale duplicate ("100g" twice for Cumin Powder - ids 36 active / 37
inactive) left stray movements and challan items pointing at the dead size,
so history split across two rows. This script repoints every reference from
each deactivated duplicate to the active twin with the same trimmed name and
the same pack weight, so the balance adds up on one row.

Safety rules (see AGENTS.md):
- Refuses the repo's live database path outright.
- Refuses to write unless --apply is given (default is a dry-run report).
- Idempotent: sizes with no remaining references are skipped automatically.

Run (from the repo root, laptop app CLOSED):
    python scripts/merge_inactive_pack_sizes.py --db <path-to-db>          # dry run
    python scripts/merge_inactive_pack_sizes.py --db <path-to-db> --apply  # write
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from decimal import Decimal  # noqa: E402

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from backend.app import models  # noqa: E402

# The repo's own live database. A repair script pointed here can damage live
# records, so it is refused even with --apply - target the laptop DB instead.
REPO_LIVE_DB = os.path.join(ROOT, "data", "masala_factory.db")

# Tables that may reference packaging_sizes.id. Anything not listed here is
# reported as a reference the script does not know how to repoint.
REFERRING_TABLES = (
    models.FinishedProductStockMovement,
    models.SalesChallanItem,
    models.SalesInvoiceItem,
    models.ProductionEntry,
    models.ProductionOutput,
    models.SizeRateHistory,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Path to the database file to repair.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the merges. Without this the script only reports.",
    )
    args = parser.parse_args()

    db_path = os.path.abspath(args.db)
    if not os.path.exists(db_path):
        print(f"ERROR: database not found: {db_path}")
        return 2
    if os.path.abspath(REPO_LIVE_DB) == db_path:
        print(
            "ERROR: refusing the repo's live data/masala_factory.db.\n"
            "Run this against the laptop database (or a copy of it)."
        )
        return 2

    engine = create_engine(f"sqlite:///{db_path}")
    db = sessionmaker(bind=engine)()
    try:
        sizes = db.query(models.PackagingSize).all()
        by_product = {}
        for s in sizes:
            by_product.setdefault(s.product_id, []).append(s)

        def identity(size):
            name = (size.size_name or "").strip().casefold()
            weight = Decimal(str(size.weight_in_kg or 0))
            return name, weight

        merged = 0
        for dead in (s for s in sizes if not s.is_active):
            name, weight = identity(dead)
            twin = next(
                (
                    t
                    for t in by_product.get(dead.product_id, [])
                    if t.is_active and identity(t) == (name, weight) and t.id != dead.id
                ),
                None,
            )
            refs = {
                tbl.__tablename__: db.query(tbl)
                .filter(tbl.packaging_size_id == dead.id)
                .count()
                for tbl in REFERRING_TABLES
            }
            total_refs = sum(refs.values())
            if twin is None:
                if total_refs:
                    print(
                        f"SKIP size {dead.id} '{dead.size_name}' (product {dead.product_id}): "
                        f"{total_refs} references but no active twin - fix manually: {refs}"
                    )
                continue
            if not total_refs:
                continue
            print(
                f"merge: {total_refs} references "
                f"size {dead.id} '{dead.size_name}' -> size {twin.id} "
                f"'{twin.size_name}' (product {dead.product_id}): {refs}"
            )
            if not args.apply:
                continue
            for tbl in REFERRING_TABLES:
                (
                    db.query(tbl)
                    .filter(tbl.packaging_size_id == dead.id)
                    .update({tbl.packaging_size_id: twin.id}, synchronize_session=False)
                )
            merged += 1

        if not args.apply:
            print("dry run - nothing written. Re-run with --apply to write.")
            return 0
        db.commit()

        # Verify: no reference may survive on a deactivated merged size.
        leftover = 0
        for dead in (s for s in sizes if not s.is_active):
            for tbl in REFERRING_TABLES:
                n = db.query(tbl).filter(tbl.packaging_size_id == dead.id).count()
                if n:
                    leftover += n
                    print(
                        f"LEFTOVER: size {dead.id} still referenced by "
                        f"{tbl.__tablename__} ({n} rows)"
                    )
        print(f"merged size pairs: {merged}")
        print(f"leftover references: {leftover}")
        return 1 if leftover else 0
    finally:
        db.close()
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
