"""Same-day order: newest first everywhere, not just challans.

The universal date rule: every date-ordered list ends `id.desc()`, because
same-day business dates tie (date-only inputs land at midnight) and ids grow
with entry time - 7pm, 4pm, 10am must list in that order. Challans already
do this (see test_challan_list_order.py); expenses, purchase invoices and
production entries did not, so same-day rows came back oldest-first.

Run: python -m pytest backend/tests/test_same_day_order.py -v
"""

import os
import sys
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402

DAY = datetime(2026, 9, 10, 0, 0, 0)


def _expense(db, description):
    cat = db.query(models.ExpenseCategory).filter(
        models.ExpenseCategory.name == "Same-day test").first()
    if not cat:
        cat = models.ExpenseCategory(name="Same-day test", is_active=True)
        db.add(cat)
        db.flush()
    db.add(models.Expense(category_id=cat.id, description=description,
                          amount=Decimal("100"), date=DAY,
                          payment_mode="cash"))
    db.commit()


def test_same_day_expenses_newest_first(db):
    _expense(db, "ten-am")
    _expense(db, "seven-pm")
    rows = m.list_expenses(db=db)
    descs = [r["description"] for r in rows]
    assert descs.index("seven-pm") < descs.index("ten-am"), descs


def _supplier(db):
    s = models.Supplier(supplier_id="S-SD", name="Same-day Sons",
                        contact_number="9000000099")
    db.add(s)
    db.commit()
    return s


def test_same_day_purchase_invoices_newest_first(db):
    s = _supplier(db)
    db.add(models.PurchaseInvoice(invoice_number="PUR-1", invoice_date=DAY,
                                  supplier_id=s.id))
    db.commit()
    db.add(models.PurchaseInvoice(invoice_number="PUR-2", invoice_date=DAY,
                                  supplier_id=s.id))
    db.commit()
    rows = m.list_purchase_invoices(db=db)
    numbers = [r["invoice_number"] for r in rows]
    assert numbers.index("PUR-2") < numbers.index("PUR-1"), numbers


def test_same_day_production_entries_newest_first(db):
    prod = models.Product(product_id="P-SD", name="Same-day Masala")
    db.add(prod)
    db.flush()
    size = models.PackagingSize(product_id=prod.id, size_name="250g",
                                weight_in_kg=Decimal("0.25"), is_active=True)
    db.add(size)
    db.commit()
    db.add(models.ProductionEntry(production_number="B-1", production_date=DAY,
                                  product_id=prod.id, packaging_size_id=size.id,
                                  status="completed"))
    db.commit()
    db.add(models.ProductionEntry(production_number="B-2", production_date=DAY,
                                  product_id=prod.id, packaging_size_id=size.id,
                                  status="completed"))
    db.commit()
    rows = m.list_production_entries(db=db)
    numbers = [r["production_number"] for r in rows]
    assert numbers.index("B-2") < numbers.index("B-1"), numbers
