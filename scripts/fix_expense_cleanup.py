"""Archive test expense rows, rename a payee spelling, deactivate a category.

Case in point: three rupee-one test entries filed under "Pooja and Functions" /
"Pooja and Other", one payee typed as "mayself" instead of "MySelf", and those
two test categories to be taken out of the category picker.

Archiving (not deleting) is what the app's own Delete button does: the row
leaves the list and the totals keep the audit trail. The payee rename is a
case-insensitive exact match, so "Myself" is never touched. A category is
deactivated (is_active = 0), not deleted: archived expenses still reference it
by foreign key, and the list endpoint only returns active rows.

Safety rules (see AGENTS.md):
- refuses the repo's live database path outright,
- dry run unless --apply is given,
- --apply refused without --backup,
- idempotent: archived rows, already-correct payees and inactive categories
  are skipped.

Run (from the repo root, laptop app CLOSED):
    python scripts/fix_expense_cleanup.py --db <path> \
        --archive 332,333,334 --rename-payee "mayself=MySelf,manjt=Manjeet" \
        --deactivate-category "Pooja and Functions,Pooja and Other"
    ... add --apply --backup to write.

--rename-payee takes one or more OLD=NEW pairs (comma separated) so a single
run merges every misspelling of the same name against one backup.
"""

import argparse
import contextlib
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sqlalchemy import create_engine, func  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from backend.app import models  # noqa: E402
from backend.app.core.database import _ist_wall  # noqa: E402

REPO_LIVE_DB = os.path.join(ROOT, "data", "masala_factory.db")


def parse_ids(raw: str) -> list[int]:
    out = []
    for raw_token in (raw or "").split(","):
        token = raw_token.strip()
        if not token:
            continue
        try:
            out.append(int(token))
        except ValueError as exc:
            raise SystemExit(f"ERROR: --archive wants comma-separated ids, got {token!r}") from exc
    return out


def parse_payees(raw: str) -> list[tuple[str, str]]:
    """OLD=NEW pairs, comma separated - one run renames every misspelling."""
    pairs = []
    for raw_token in (raw or "").split(","):
        token = raw_token.strip()
        if not token:
            continue
        if "=" not in token:
            raise SystemExit("ERROR: --rename-payee wants OLD=NEW, e.g. mayself=MySelf")
        old, new = (part.strip() for part in token.split("=", 1))
        if not old or not new:
            raise SystemExit("ERROR: --rename-payee wants OLD=NEW, e.g. mayself=MySelf")
        pairs.append((old, new))
    return pairs


def parse_names(raw: str) -> list[str]:
    return [t.strip() for t in (raw or "").split(",") if t.strip()]


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
    if not args.archive and not args.rename_payee and not args.deactivate_category:
        errors.append("nothing to do: pass --archive, --rename-payee and/or --deactivate-category.")
    return errors


def report(db, ids: list[int], rename, names: list[str]) -> tuple[list, int]:
    """Dry-run report. Returns (expenses to archive, payee rows to rename)."""
    todo = []
    for eid in ids:
        e = db.get(models.Expense, eid)
        if e is None:
            print(f"  expense {eid}: NOT FOUND - skipped")
            continue
        cat = e.category.name if e.category else "?"
        if e.is_archived:
            print(f"  expense {eid} ({cat}, {e.amount}): already archived - skipped")
            continue
        print(
            f"  expense {eid} ({cat}, {e.amount}, {e.date}, "
            f"'{e.description}', payee '{e.payee or ''}'): -> archived"
        )
        todo.append(e)
    payee_rows = 0
    for old, new in rename:
        rows = (
            db.query(models.Expense)
            .filter(func.lower(models.Expense.payee) == old.lower())
            .count()
        )
        payee_rows += rows
        print(f"  payee '{old}' -> '{new}': {rows} row(s)")
    for name in names:
        cat = (
            db.query(models.ExpenseCategory)
            .filter(func.lower(models.ExpenseCategory.name) == name.lower())
            .first()
        )
        if cat is None:
            print(f"  category '{name}': NOT FOUND - skipped")
            continue
        if not cat.is_active:
            print(f"  category '{name}' (id {cat.id}): already inactive - skipped")
            continue
        used = db.query(models.Expense).filter(models.Expense.category_id == cat.id).count()
        print(
            f"  category '{cat.name}' (id {cat.id}, {used} expense row(s) keep "
            "their history): -> deactivated"
        )
    return todo, payee_rows


def apply_fix(db_path: str, ids: list[int], rename, names: list[str]) -> int:
    engine = create_engine(f"sqlite:///{db_path}")
    db = sessionmaker(bind=engine)()
    try:
        archived = 0
        for eid in ids:
            e = db.get(models.Expense, eid)
            if e is None or e.is_archived:
                continue
            e.is_archived = True
            e.archived_at = _ist_wall()
            archived += 1
        renamed = 0
        for old, new in rename:
            renamed += (
                db.query(models.Expense)
                .filter(func.lower(models.Expense.payee) == old.lower())
                .update({models.Expense.payee: new}, synchronize_session=False)
            )
        deactivated = 0
        for name in names:
            cat = (
                db.query(models.ExpenseCategory)
                .filter(func.lower(models.ExpenseCategory.name) == name.lower())
                .first()
            )
            if cat is None or not cat.is_active:
                continue
            cat.is_active = False
            deactivated += 1
        db.commit()
        print(
            f"applied: {archived} archived, {renamed} payee row(s) renamed, "
            f"{deactivated} categor(y/ies) deactivated"
        )
        return _verify(db, ids, rename, names)
    finally:
        db.close()
        engine.dispose()


def _verify(db, ids: list[int], rename, names: list[str]) -> int:
    """Re-read from the database; 0 == the intended end state holds."""
    live = 0
    for eid in ids:
        e = db.get(models.Expense, eid)
        if e is not None and not e.is_archived:
            live += 1
    print(f"verified: target expenses still active: {live}")
    bad = 0
    for old, new in rename:
        # Case-only renames (mayself -> MySelf) mean the misspelling still
        # matches case-insensitively once fixed, so leftover rows are counted
        # on spelling, not on case.
        left = (
            db.query(models.Expense)
            .filter(func.lower(models.Expense.payee) == old.lower())
            .filter(models.Expense.payee != new)
            .count()
        )
        bad += left
        exact = (
            db.query(models.Expense)
            .filter(models.Expense.payee == new)
            .count()
        )
        print(f"verified: '{old}' rows left: {left}; '{new}' rows now: {exact}")
    still_active = 0
    for name in names:
        cat = (
            db.query(models.ExpenseCategory)
            .filter(func.lower(models.ExpenseCategory.name) == name.lower())
            .first()
        )
        if cat is None:
            continue
        if cat.is_active:
            still_active += 1
        print(f"verified: category '{cat.name}' active = {bool(cat.is_active)}")
    return live + bad + still_active


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Path to the database file to repair.")
    parser.add_argument(
        "--archive", default="", help="Comma-separated expenses.id values to archive."
    )
    parser.add_argument(
        "--rename-payee",
        default="",
        help="OLD=NEW payee spelling, case-insensitive exact match; "
        "comma separate to merge several.",
    )
    parser.add_argument(
        "--deactivate-category",
        default="",
        help="Comma-separated category names to take out of the picker (is_active=0).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the fixes. Without this the script only reports.",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="Copy the database to <db>.fix_expense_cleanup_bak before writing.",
    )
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ids = parse_ids(args.archive)
    rename = parse_payees(args.rename_payee)
    names = parse_names(args.deactivate_category)
    db_path = os.path.abspath(args.db)
    errors = validate(args, db_path)
    if errors:
        for err in errors:
            print(f"ERROR: {err}")
        return 2

    engine = create_engine(f"sqlite:///{db_path}")
    db = sessionmaker(bind=engine)()
    try:
        report(db, ids, rename, names)
    finally:
        db.close()
        engine.dispose()

    if not args.apply:
        print("dry run - nothing written. Re-run with --apply (and --backup) to write.")
        return 0

    if args.backup:
        bak = f"{db_path}.fix_expense_cleanup_bak"
        shutil.copy2(db_path, bak)
        print(f"backup written: {bak}")
    return apply_fix(db_path, ids, rename, names)


if __name__ == "__main__":
    sys.exit(main())
