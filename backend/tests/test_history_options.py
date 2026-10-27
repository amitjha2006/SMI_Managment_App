"""History pickers: expense payees/descriptions and adjustment reasons.

First entry is typed by hand; later forms pick from history via datalists
fed by /expenses/history and /stock-adjustments/reasons.

Run: python -m pytest backend/tests/test_history_options.py -v
"""

import os
import sys
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _expense(db, cat_id, desc, payee):
    db.add(models.Expense(category_id=cat_id, description=desc,
                          amount=Decimal("100"), date=datetime(2026, 9, 10),
                          payment_mode="cash", payee=payee))
    db.commit()


def test_expense_history_distinct_recent_first(db):
    cat = models.ExpenseCategory(name="Hist test", is_active=True)
    db.add(cat)
    db.commit()
    _expense(db, cat.id, "Power bill", "MSEB")
    _expense(db, cat.id, "Diesel", "Fuel Station")
    _expense(db, cat.id, "Power bill Sep", "mseb ")
    out = m.expense_history_options(db)
    # Most-recent casing wins ("mseb " beats the older "MSEB").
    assert out["payees"] == ["mseb", "Fuel Station"], out
    assert out["descriptions"][0] == "Power bill Sep", out
    assert len(out["descriptions"]) == 3, out


def test_adjustment_reasons_merge_both_tables(db):
    mat = models.RawMaterial(material_id="RM-H1", name="Hist Raw", unit="kg")
    db.add(mat)
    db.commit()
    db.add(models.RawMaterialStockMovement(
        raw_material_id=mat.id, movement_type="adjustment",
        quantity=Decimal("1"), unit="kg", notes="damage"))
    db.add(models.RawMaterialStockMovement(
        raw_material_id=mat.id, movement_type="consumption",
        quantity=Decimal("1"), unit="kg", notes="production use"))
    db.commit()
    prod = models.Product(product_id="P-H1", name="Hist Prod")
    db.add(prod)
    db.flush()
    size = models.PackagingSize(
        product_id=prod.id, size_name="250g",
        weight_in_kg=Decimal("0.25"), rate_per_kg=Decimal("200"), is_active=True)
    db.add(size)
    db.commit()
    db.add(models.FinishedProductStockMovement(
        product_id=prod.id, packaging_size_id=size.id,
        movement_type="adjustment", reference_type="test",
        reference_id=0, quantity=Decimal("2"), notes="recount"))
    db.commit()
    out = m.stock_adjustment_reasons(db)
    assert out == {"reasons": ["recount", "damage"]}, out


def test_history_picklers_wired_in_forms():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "expenses.html"), encoding="utf-8") as fh:
        exp = fh.read()
    assert 'list="exp-payee-list"' in exp
    assert 'list="exp-desc-list"' in exp
    assert "/expenses/history" in exp
    with open(os.path.join(root, "frontend", "pages", "inventory.html"), encoding="utf-8") as fh:
        inv = fh.read()
    assert 'list="adj-reason-list"' in inv
    assert "/stock-adjustments/reasons" in inv
