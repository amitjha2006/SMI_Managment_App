"""Repair a packaging size whose stored weight_in_kg rounded away the truth.

Case in point: Garam Masala's "7g" size (id 57) was stored as 0.01 kg (10g)
because its Weight box was filled from a 2-decimal formatter, so every
recording made through it (opening stock in kg, challan line weights and
amounts) is consistent with 0.01 rather than the real 0.007. This script

1. corrects the size's weight_in_kg,
2. recomputes every finished-product stock movement's kg quantity from its
   (reliable, integer) packets column at the corrected weight, and
3. recomputes every sales-challan line for the size (weight, amount) and the
   affected challans' totals.

It refuses to *bill-adjust* a challan that already carries money (payments
received, a non-zero outstanding, or a tax invoice) unless --force-money is
given; those need a human look first. Draft challans with nothing paid - the
common case - are corrected outright.

Safety rules (see AGENTS.md):
- Refuses the repo's live database path outright.
- Refuses to write unless --apply is given (default is a dry-run report).
- Refuses --apply without a backup (pass --backup to make one).
- Idempotent: a size already at the target weight is skipped.

Run (from the repo root, laptop app CLOSED):
    python scripts/fix_size_weight.py --db <path> --size-id 57 --weight 0.007          # dry run
    python scripts/fix_size_weight.py --db <path> --size-id 57 --weight 0.007 --apply --backup
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

D2 = Decimal("0.01")
D3 = Decimal("0.001")


def q2(v) -> Decimal:
    return Decimal(str(v)).quantize(D2, rounding=ROUND_HALF_UP)


def q3(v) -> Decimal:
    return Decimal(str(v)).quantize(D3, rounding=ROUND_HALF_UP)


def validate(args, db_path: str) -> list[str]:
    """Return human-readable reasons the run must abort, empty when safe."""
    errors = []
    if Decimal(args.weight) <= 0:
        errors.append("--weight must be positive.")
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


def print_report(db: Session, size, new_weight: Decimal, force_money: bool):
    """Dry-run report. Returns (items_to_fix, skipped_challans)."""
    product = db.get(models.Product, size.product_id)
    print(
        f"size {size.id} '{size.size_name}' "
        f"(product {size.product_id} '{product.name if product else '?'}'): "
        f"weight {size.weight_in_kg} -> {new_weight}"
    )

    # Stock movements: kg quantity is derived from the packets column, which
    # the app stores as reliable integers. Historical rows were
    # kg = packets * old weight, so the correction is the same formula at the
    # new weight.
    movements = (
        db.query(models.FinishedProductStockMovement)
        .filter(models.FinishedProductStockMovement.packaging_size_id == size.id)
        .order_by(models.FinishedProductStockMovement.id)
        .all()
    )
    for m in movements:
        old_qty = Decimal(str(m.quantity))
        new_qty = q3(Decimal(m.packets or 0) * new_weight)
        if old_qty != new_qty:
            print(
                f"  movement {m.id} {m.movement_type}: quantity "
                f"{old_qty} -> {new_qty} (packets {m.packets})"
            )

    # Challan lines for the size whose parent carries no money: weight is
    # quantity times the new pack weight, and the billed amount follows the
    # rate per kg. Money-carrying challans are projected but not fixed
    # unless --force-money was given.
    items = (
        db.query(models.SalesChallanItem)
        .filter(models.SalesChallanItem.packaging_size_id == size.id)
        .order_by(models.SalesChallanItem.id)
        .all()
    )
    item_fix = {}  # item id -> (new_weight, new_amount)
    skipped = {}  # challan id -> challan number
    for item in items:
        challan = item.challan
        money_locked = bool(
            (challan.paid_amount or 0) != 0
            or (challan.outstanding_amount or 0) != 0
            or challan.invoice is not None
        )
        if money_locked and not force_money:
            skipped[item.challan_id] = challan.challan_number
            continue
        new_line_weight = q3(Decimal(str(item.quantity)) * new_weight)
        new_amount = q2(new_line_weight * Decimal(str(item.rate_per_kg)))
        if new_line_weight != Decimal(str(item.weight)) or new_amount != Decimal(str(item.amount)):
            print(
                f"  item {item.id} (challan {item.challan_id} "
                f"'{challan.challan_number}'): weight {item.weight} -> "
                f"{new_line_weight}, amount {item.amount} -> {new_amount} "
                f"(rate {item.rate_per_kg}/kg)"
            )
            item_fix[item.id] = (new_line_weight, new_amount)

    for challan in sorted(
        {item.challan for item in items if item.challan_id not in skipped},
        key=lambda c: c.id,
    ):
        projected = _project_challan_totals(db, challan, size.id, new_weight)
        stored_w = Decimal(str(challan.total_weight))
        stored_p = Decimal(str(challan.total_price))
        if projected[0] != stored_w or projected[1] != stored_p:
            print(
                f"  challan {challan.id} '{challan.challan_number}': "
                f"total_weight {stored_w} -> {projected[0]}, total_price "
                f"{stored_p} -> {projected[1]} (status {challan.status}, "
                f"paid {challan.paid_amount}, outstanding {challan.outstanding_amount})"
            )

    if skipped:
        print(
            f"skipped challans carrying money: {sorted(skipped.items())} - "
            "their lines and totals would NOT change (re-run with "
            "--force-money after manual review)."
        )
    return item_fix, skipped


def _project_challan_totals(db: Session, challan, size_id: int, new_weight: Decimal):
    """Recompute (total_weight, total_price) from all lines, using the new
    weight for this size's lines and stored values for the rest."""
    tot_w = Decimal(0)
    tot_p = Decimal(0)
    for line in db.query(models.SalesChallanItem).filter(
        models.SalesChallanItem.challan_id == challan.id
    ).order_by(models.SalesChallanItem.id):
        if line.packaging_size_id == size_id:
            w = q3(Decimal(str(line.quantity)) * new_weight)
            a = q2(w * Decimal(str(line.rate_per_kg)))
        else:
            w, a = Decimal(str(line.weight)), Decimal(str(line.amount))
        tot_w += w
        tot_p += a
    return q3(tot_w), q2(tot_p)


def apply_fix(db_path: str, size_id: int, new_weight: Decimal, force_money: bool) -> int:
    """Write the correction in one committed transaction, then verify."""
    engine = create_engine(f"sqlite:///{db_path}")
    db = sessionmaker(bind=engine)()
    try:
        size = db.get(models.PackagingSize, size_id)
        size.weight_in_kg = new_weight
        for m in (
            db.query(models.FinishedProductStockMovement)
            .filter(models.FinishedProductStockMovement.packaging_size_id == size.id)
            .all()
        ):
            m.quantity = q3(Decimal(m.packets or 0) * new_weight)
        items = (
            db.query(models.SalesChallanItem)
            .filter(models.SalesChallanItem.packaging_size_id == size.id)
            .all()
        )
        touched = set()
        for item in items:
            challan = item.challan
            money_locked = bool(
                (challan.paid_amount or 0) != 0
                or (challan.outstanding_amount or 0) != 0
                or challan.invoice is not None
            )
            if money_locked and not force_money:
                continue
            item.weight = q3(Decimal(str(item.quantity)) * new_weight)
            item.amount = q2(Decimal(str(item.weight)) * Decimal(str(item.rate_per_kg)))
            touched.add(item.challan_id)
        for cid in touched:
            challan = db.get(models.SalesChallan, cid)
            challan.total_weight = q3(sum(
                (Decimal(str(line.weight)) for line in challan.items), Decimal(0)))
            challan.total_price = q2(sum(
                (Decimal(str(line.amount)) for line in challan.items), Decimal(0)))
        db.commit()

        return _verify(db, size_id, new_weight, touched)
    finally:
        db.close()
        engine.dispose()


def _verify(db: Session, size_id: int, new_weight: Decimal, touched: set) -> int:
    """Check nothing remains inconsistent for the size; 0 == all consistent."""
    size = db.get(models.PackagingSize, size_id)
    print(f"verified: size {size.id} weight_in_kg = {size.weight_in_kg}")
    bad_mov = (
        db.query(models.FinishedProductStockMovement)
        .filter(
            models.FinishedProductStockMovement.packaging_size_id == size.id,
            models.FinishedProductStockMovement.quantity
            != models.FinishedProductStockMovement.packets * new_weight,
        )
        .count()
    )
    print(f"verified: movements inconsistent with packets x {new_weight}: {bad_mov}")
    bad_line = 0
    lines = (
        db.query(models.SalesChallanItem)
        .filter(models.SalesChallanItem.packaging_size_id == size.id)
        .all()
    )
    for line in lines:
        expect = q3(Decimal(str(line.quantity)) * new_weight)
        if Decimal(str(line.weight)) != expect:
            bad_line += 1
    print(f"verified: challan lines inconsistent with quantity x {new_weight}: {bad_line}")
    bad_tot = 0
    for cid in touched:
        challan = db.get(models.SalesChallan, cid)
        tw = q3(sum((Decimal(str(line.weight)) for line in challan.items), Decimal(0)))
        tp = q2(sum((Decimal(str(line.amount)) for line in challan.items), Decimal(0)))
        if tw != Decimal(str(challan.total_weight)) or tp != Decimal(str(challan.total_price)):
            bad_tot += 1
    print(f"verified: challan totals drifting from their lines: {bad_tot}")
    return bad_mov + bad_line + bad_tot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Path to the database file to repair.")
    parser.add_argument("--size-id", type=int, required=True, help="packaging_sizes.id to fix.")
    parser.add_argument("--weight", required=True, help="Corrected weight_in_kg, e.g. 0.007.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the fixes. Without this the script only reports.",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="Copy the database to <db>.fix_size_weight_bak before writing.",
    )
    parser.add_argument(
        "--force-money",
        action="store_true",
        help="Allow bill corrections on challans that already carry money "
        "(paid amount, outstanding, or a tax invoice). Default: skip them "
        "with a warning.",
    )
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
        size = db.get(models.PackagingSize, args.size_id)
        if size is None:
            print(f"ERROR: packaging size {args.size_id} not found.")
            return 2
        new_weight = Decimal(args.weight)
        if Decimal(str(size.weight_in_kg)) == new_weight:
            print(f"size {size.id} '{size.size_name}' already at {new_weight} - nothing to do.")
            return 0
        print_report(db, size, new_weight, args.force_money)
        if not args.apply:
            print("dry run - nothing written. Re-run with --apply (and --backup) to write.")
            return 0
    finally:
        db.close()
        engine.dispose()

    if args.backup:
        bak = f"{db_path}.fix_size_weight_bak"
        shutil.copy2(db_path, bak)
        print(f"backup written: {bak}")
    return apply_fix(db_path, args.size_id, new_weight, args.force_money)


if __name__ == "__main__":
    sys.exit(main())
