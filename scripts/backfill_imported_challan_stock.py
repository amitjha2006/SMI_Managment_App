"""One-time backfill: post SALES stock movements for imported challans that lack them.

The old importer created challans already confirmed/paid but never posted the
finished-goods SALES movements, so stock added through Stock Adjustments was
never reduced by the imported (already-sold) challans. Run this once against
the laptop database after updating the code; the importer now posts stock
itself, and this script repairs what the old importer left behind.

Safety rules (see AGENTS.md):
- Refuses the repo's live database path outright.
- Refuses to write unless --apply is given (default is a dry-run report).
- Idempotent: challans that already have movements are skipped, so it can be
  safely run twice.

Run (from the repo root, laptop app CLOSED):
    python scripts/backfill_imported_challan_stock.py --db <path-to-db>          # dry run
    python scripts/backfill_imported_challan_stock.py --db <path-to-db> --apply  # write
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
from backend.app.models.sales import ChallanStatus  # noqa: E402
from backend.app.services.stock_posting import (  # noqa: E402
    challan_stock_posted,
    post_challan_sales_movements,
)

# The repo's own live database. A backfill script pointed here can damage live
# records, so it is refused even with --apply - target the laptop DB instead.
REPO_LIVE_DB = os.path.join(ROOT, "data", "masala_factory.db")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Path to the database file to repair.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the movements. Without this the script only reports.",
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
        total = (
            db.query(models.SalesChallan)
            .filter(models.SalesChallan.is_archived.is_(False))
            .filter(models.SalesChallan.status != ChallanStatus.DRAFT)
            .count()
        )
        unposted = []
        for ch in (
            db.query(models.SalesChallan)
            .filter(models.SalesChallan.is_archived.is_(False))
            .filter(models.SalesChallan.status != ChallanStatus.DRAFT)
            .order_by(models.SalesChallan.id)
            .all()
        ):
            if not challan_stock_posted(db, ch.id):
                unposted.append(ch)

        print(f"database:             {db_path}")
        print(f"confirmed challans:   {total}")
        print(f"missing movements:    {len(unposted)}")
        if not args.apply:
            print("dry run - nothing written. Re-run with --apply to write.")
            return 0

        posted = 0
        for ch in unposted:
            if post_challan_sales_movements(db, ch, ch.items or []):
                posted += 1
        db.commit()
        if not unposted:
            print("nothing to backfill.")

        # Verify: every posted challan's movement weight must equal its item weight.
        bad = 0
        for ch in (
            db.query(models.SalesChallan)
            .filter(models.SalesChallan.is_archived.is_(False))
            .filter(models.SalesChallan.status != ChallanStatus.DRAFT)
            .all()
        ):
            item_weight = sum(Decimal(str(i.weight or 0)) for i in (ch.items or []))
            move_weight = sum(
                Decimal(str(m.quantity or 0))
                for m in db.query(models.FinishedProductStockMovement)
                .filter(
                    models.FinishedProductStockMovement.reference_type == "sales_challan",
                    models.FinishedProductStockMovement.reference_id == ch.id,
                )
                .all()
            )
            if item_weight != move_weight:
                bad += 1
                print(
                    f"MISMATCH {ch.challan_number}: items {item_weight} vs movements {move_weight}"
                )
        remaining = sum(
            1
            for ch in (
                db.query(models.SalesChallan)
                .filter(models.SalesChallan.is_archived.is_(False))
                .filter(models.SalesChallan.status != ChallanStatus.DRAFT)
                .all()
            )
            if not challan_stock_posted(db, ch.id)
        )
        print(f"backfilled challans:  {posted}")
        print(f"weight mismatches:    {bad}")
        print(f"still unposted:       {remaining}")
        return 1 if bad or remaining else 0
    finally:
        db.close()
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
