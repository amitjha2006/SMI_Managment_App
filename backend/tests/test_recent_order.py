"""Recent transactions: newest first, expenses included.

Two defects: the endpoint never listed expenses at all (so October
expenses could not appear on top), and the dashboard date cells render
"M/D/YYYY" strings that the shared digit-strip sorter misorders across
months with different digit counts (9/28 sorts above 10/4). The table
must sort its date column by timestamp via the shared sortValue hook.

Run: python -m pytest backend/tests/test_recent_order.py -v
"""

import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _req():
    return SimpleNamespace(headers={}, client=None)


def _expense(db, date, amount=100):
    cat = db.query(models.ExpenseCategory).filter(
        models.ExpenseCategory.name == "Recent test").first()
    if not cat:
        cat = models.ExpenseCategory(name="Recent test", is_active=True)
        db.add(cat)
        db.flush()
    db.add(models.Expense(category_id=cat.id, description="recent",
                          amount=Decimal(str(amount)), date=date,
                          payment_mode="cash"))
    db.commit()


def test_expenses_appear_newest_first(db):
    _expense(db, datetime.now() - timedelta(days=40), 50)
    _expense(db, datetime.now(), 100)
    rows = m.recent_transactions(limit=100, db=db)
    exps = [r for r in rows if r["type"] == "Expense"]
    assert len(exps) == 2, rows
    first = [r for r in rows if r["type"] == "Expense"][0]
    assert first["date"] >= exps[1]["date"], rows
    assert rows[0]["date"] >= rows[-1]["date"], "endpoint must return newest first"


def test_archived_expenses_stay_out(db):
    _expense(db, datetime.now(), 100)
    row = db.query(models.Expense).order_by(models.Expense.id.desc()).first()
    row.is_archived = True
    db.commit()
    rows = m.recent_transactions(limit=100, db=db)
    assert all(r["type"] != "Expense" for r in rows), rows


def test_same_day_rows_order_by_recorded_time(db):
    # Two records, same business date, entered hours apart: the later
    # entry must list first. The sort key must be recorded time, because
    # date-only business dates all tie at midnight.
    from sqlalchemy import text as _text

    day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    _expense(db, day, 100)
    _expense(db, day, 200)
    db.execute(_text(
        "UPDATE expenses SET created_at = :early WHERE amount = '100'"),
        {"early": day.isoformat()})
    db.execute(_text(
        "UPDATE expenses SET created_at = :late WHERE amount = '200'"),
        {"late": (day + timedelta(hours=7)).isoformat()})
    db.commit()
    rows = m.recent_transactions(limit=100, db=db)
    exps = [r for r in rows if r["type"] == "Expense"]
    assert len(exps) == 2, rows
    assert Decimal(str(exps[0]["amount"])) == Decimal("200"), exps
    assert Decimal(str(exps[1]["amount"])) == Decimal("100"), exps


def test_recent_endpoint_carries_recorded_at(db):
    _expense(db, datetime.now(), 100)
    rows = m.recent_transactions(limit=100, db=db)
    exps = [r for r in rows if r["type"] == "Expense"]
    assert len(exps) == 1, rows
    assert exps[0].get("created_at"), (
        "recent rows must carry when each record was entered"
    )


def test_expense_reference_shows_description_not_code(db):
    _expense(db, datetime.now(), 100)
    rows = m.recent_transactions(limit=100, db=db)
    exps = [r for r in rows if r["type"] == "Expense"]
    assert len(exps) == 1, rows
    assert exps[0]["reference"] == "recent", exps
    assert not str(exps[0]["reference"]).startswith("EXP-"), exps


def test_from_date_narrows_to_business_date(db):
    _expense(db, datetime.now() - timedelta(days=40), 50)
    _expense(db, datetime.now(), 100)
    today = datetime.now().date().isoformat()
    rows = m.recent_transactions(limit=100, from_date=today, db=db)
    exps = [r for r in rows if r["type"] == "Expense"]
    assert len(exps) == 1, rows
    assert Decimal(str(exps[0]["amount"])) == Decimal("100"), exps


def test_date_window_is_inclusive_both_ends(db):
    day = (datetime.now() - timedelta(days=3)).date().isoformat()
    _expense(db, datetime.now() - timedelta(days=3), 100)
    rows = m.recent_transactions(limit=100, from_date=day, to_date=day, db=db)
    assert len([r for r in rows if r["type"] == "Expense"]) == 1, rows


def test_limit_applies_after_filtering(db):
    _expense(db, datetime.now() - timedelta(days=40), 50)
    _expense(db, datetime.now(), 100)
    rows = m.recent_transactions(limit=1, db=db)
    assert len(rows) == 1, rows
    assert Decimal(str(rows[0]["amount"])) == Decimal("100"), rows


def test_bad_dates_are_ignored_not_fatal(db):
    _expense(db, datetime.now(), 100)
    rows = m.recent_transactions(
        limit=100, from_date="not-a-date", to_date="32/13/2026", db=db)
    assert len([r for r in rows if r["type"] == "Expense"]) == 1, rows


def test_recent_card_has_date_and_fy_filters():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "dashboard.html"), encoding="utf-8") as fh:
        dash = fh.read()
    for el in ('id="txn-from"', 'id="txn-to"', 'id="txn-fy-only"',
               'id="txn-type-filter"', "from_date=", "applyFyStart"):
        assert el in dash, f"recent-transactions date/FY filter missing: {el}"


def test_recent_rows_carry_recorded_at():
    # The small time must be when the record was entered, not the
    # date-only business date (midnight by design). Every row therefore
    # carries its created_at for the display to use.
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "dashboard.html"), encoding="utf-8") as fh:
        dash = fh.read()
    i = dash.find("function renderRecentTxns()")
    assert i >= 0
    body = dash[i:i + 2500]
    assert "created_at" in body, (
        "recent table ignores recorded time and re-shows midnight business dates"
    )


def _read_rel(rel):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


def test_recent_table_sorts_date_by_timestamp():
    dash = _read_rel("frontend/pages/dashboard.html")
    i = dash.find("function renderRecentTxns()")
    assert i >= 0
    body = dash[i:i + 2500]
    assert "sortValue" in body and "date" in body, (
        "recent table must sort its date column by timestamp, not digit-strip"
    )
