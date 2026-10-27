"""Money stays at 2 decimals in the database, not just on screen.

SQLite ignores Numeric(15,2) scale, so only Python-side rounding enforces
it. A central flush hook rounds every scale-2 money/rate column with
ROUND_HALF_UP on insert and update; quantity columns (scale 3) are
untouched.

Run: python -m pytest backend/tests/test_money_scale.py -v
"""

import os
import sys
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app import models  # noqa: E402


def _category(db):
    cat = models.ExpenseCategory(name="Scale")
    db.add(cat)
    db.commit()
    return cat


def test_money_rounds_half_up_on_insert(db):
    cat = _category(db)
    exp = models.Expense(
        category_id=cat.id, description="scale", amount=Decimal("10.005"),
        date=datetime(2026, 9, 5, 10, 0, 0), payment_mode="cash",
    )
    prod = models.Product(
        product_id="P-SC1", name="Scale Masala",
        base_rate_per_kg=Decimal("199.999"), gst_rate=Decimal("5.555"),
    )
    db.add_all([exp, prod])
    db.commit()
    assert exp.amount == Decimal("10.01")
    assert prod.base_rate_per_kg == Decimal("200.00")
    assert prod.gst_rate == Decimal("5.56")


def test_money_rounds_down_and_floats(db):
    cat = _category(db)
    exp = models.Expense(
        category_id=cat.id, description="scale", amount=Decimal("10.004"),
        date=datetime(2026, 9, 5, 10, 0, 0), payment_mode="cash",
    )
    exp2 = models.Expense(
        category_id=cat.id, description="float", amount=10.005,
        date=datetime(2026, 9, 5, 10, 0, 0), payment_mode="cash",
    )
    db.add_all([exp, exp2])
    db.commit()
    assert exp.amount == Decimal("10.00")
    assert exp2.amount == Decimal("10.01")


def test_quantities_keep_three_decimals(db):
    mat = models.RawMaterial(
        material_id="RM-SC", name="Scale Spice", unit="kg",
        current_stock=Decimal("10.001"),
    )
    db.add(mat)
    db.commit()
    assert mat.current_stock == Decimal("10.001")


def test_money_rounds_on_update(db):
    cat = _category(db)
    exp = models.Expense(
        category_id=cat.id, description="scale", amount=Decimal("10"),
        date=datetime(2026, 9, 5, 10, 0, 0), payment_mode="cash",
    )
    db.add(exp)
    db.commit()
    exp.amount = Decimal("7.777")
    db.commit()
    assert exp.amount == Decimal("7.78")
