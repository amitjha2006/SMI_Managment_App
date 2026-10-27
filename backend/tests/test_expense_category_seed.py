"""Standard masala-industry expense categories seed.

A fresh or cleaned database starts with zero categories, leaving the
expense form's dropdown empty until someone types one. The seed ensures
the standard list exists (additive only: existing rows are never touched,
re-running adds nothing).

Run: python -m pytest backend/tests/test_expense_category_seed.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app.core.database import (  # noqa: E402
    STANDARD_EXPENSE_CATEGORIES,
    _seed_expense_categories,
)
from backend.app.models.expense import ExpenseCategory  # noqa: E402


def _names(db):
    return sorted(r[0] for r in db.query(ExpenseCategory.name).all())


def test_seed_adds_standard_list(db):
    assert _seed_expense_categories(db) == len(STANDARD_EXPENSE_CATEGORIES)
    assert _names(db) == sorted(STANDARD_EXPENSE_CATEGORIES)
    assert db.query(ExpenseCategory).filter(ExpenseCategory.is_active == False).count() == 0  # noqa: E712


def test_seed_is_idempotent(db):
    assert _seed_expense_categories(db) == len(STANDARD_EXPENSE_CATEGORIES)
    assert _seed_expense_categories(db) == 0
    assert len(_names(db)) == len(STANDARD_EXPENSE_CATEGORIES)


def test_seed_preserves_existing(db):
    db.add(ExpenseCategory(name="My Custom", is_active=True))
    db.commit()
    _seed_expense_categories(db)
    assert "My Custom" in _names(db)
    assert len(_names(db)) == len(STANDARD_EXPENSE_CATEGORIES) + 1
