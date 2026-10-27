"""New expenses capture their timestamp.

An expense saved today must carry created_at (IST) through the create
route and the list payload, which is what the list's small timestamp
renders. If this fails, the list genuinely has nothing to show.

Run: python -m pytest backend/tests/test_expense_timestamp.py -v
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _req():
    return SimpleNamespace(headers={}, client=None)


def test_new_expense_carries_created_at(db):
    cat = models.ExpenseCategory(name="Timestamp test", is_active=True)
    db.add(cat)
    db.commit()
    out = m.create_expense({
        "category_id": cat.id, "description": "timestamp check",
        "amount": 250, "date": datetime.now().isoformat(),
        "payment_mode": "cash",
    }, _req(), db)
    assert out["created_at"] is not None
    rows = m.list_expenses(db=db)
    saved = [r for r in rows if r["description"] == "timestamp check"]
    assert len(saved) == 1, rows
    assert saved[0]["created_at"] is not None
    stored = saved[0]["created_at"]
    stored = stored.replace(tzinfo=None) if getattr(stored, "tzinfo", None) else stored
    skew = abs((datetime.now(timezone.utc).replace(tzinfo=None)
                + timedelta(hours=5, minutes=30) - stored).total_seconds())
    assert skew < 600, f"created_at is not IST wall time: {stored}"
