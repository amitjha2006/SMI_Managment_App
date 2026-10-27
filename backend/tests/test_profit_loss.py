"""Monthly/yearly profit summary: sales minus purchases, expenses, payroll.

Sales = non-draft, non-archived challans in the period. Out = purchase
invoices + expenses in the period + payroll runs booked to the period.
Archived rows never move the figures.

Run: python -m pytest backend/tests/test_profit_loss.py -v
"""

import os
import sys
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _req():
    return SimpleNamespace(query_params={"reason": "test change note"}, headers={}, client=None)


def _setup(db):
    c = models.Customer(customer_id="CUST-PL1", name="PL Co",
                        contact_number="9000000121")
    db.add(c)
    s = models.Supplier(supplier_id="SUP-PL1", name="PL Sup",
                        contact_number="9000000122")
    db.add(s)
    prod = models.Product(product_id="P-PL1", name="PL Masala")
    db.add(prod)
    db.flush()
    size = models.PackagingSize(product_id=prod.id, size_name="250g",
                                weight_in_kg=Decimal("0.25"),
                                rate_per_kg=Decimal("200"), is_active=True)
    db.add(size)
    db.commit()
    db.add(models.FinishedProductStockMovement(
        product_id=prod.id, packaging_size_id=size.id,
        movement_type="production", reference_type="test",
        reference_id=0, quantity=Decimal("100")))
    db.commit()
    return {"c": c, "s": s, "prod": prod, "size": size}


def _month():
    now = datetime.now()
    return now.strftime("%Y-%m"), now


def _sale(db, s, total=2000, draft=False):
    ch = m.create_challan({
        "customer_id": s["c"].id,
        "challan_date": datetime.now().isoformat(),
        "items": [{"product_id": s["prod"].id,
                   "packaging_size_id": s["size"].id,
                   "quantity": 40, "weight": 10,
                   "rate_per_kg": Decimal(total) / Decimal("10")}],
    }, _req(), db)
    row = db.query(models.SalesChallan).filter(
        models.SalesChallan.id == ch["id"]).one()
    if not draft:
        m.set_challan_status(row.id, {"status": "confirmed"}, _req(), db)
    return row


def test_month_profit_counts_each_leg(db):
    s = _setup(db)
    period, now = _month()
    _sale(db, s)  # +2000 sales
    db.add(models.PurchaseInvoice(
        invoice_number="INV-PL-1", invoice_date=now, supplier_id=s["s"].id,
        total_amount=Decimal("800"), paid_amount=Decimal("0"),
        outstanding_amount=Decimal("800")))
    cat = models.ExpenseCategory(name="PL", is_active=True)
    db.add(cat)
    db.flush()
    db.add(models.Expense(category_id=cat.id, description="pl",
                          amount=Decimal("100"), date=now, payment_mode="cash"))
    emp = models.Employee(employee_id="EMP-PL1", name="PL Staff",
                          employee_type="full_time")
    db.add(emp)
    db.flush()
    db.add(models.SalaryPayment(employee_id=emp.id, period_month=period,
                                payment_date=now, net_pay=Decimal("500")))
    db.commit()
    out = m.profit_loss(period=period, db=db)
    assert Decimal(str(out["sales"])) == Decimal("2000"), out
    assert Decimal(str(out["purchases"])) == Decimal("800"), out
    assert Decimal(str(out["expenses"])) == Decimal("100"), out
    assert Decimal(str(out["payroll"])) == Decimal("500"), out
    assert Decimal(str(out["profit"])) == Decimal("600"), out


def test_drafts_archived_and_other_periods_stay_out(db):
    s = _setup(db)
    period, _ = _month()
    _sale(db, s, draft=True)  # draft: not a sale yet
    arch = _sale(db, s)
    arch.is_archived = True
    db.commit()
    out = m.profit_loss(period=period, db=db)
    assert Decimal(str(out["sales"])) == Decimal("0"), out
    assert Decimal(str(out["profit"])) == Decimal("0"), out


def test_year_shape_sums_months(db):
    s = _setup(db)
    period, _ = _month()
    _sale(db, s)
    year = period[:4]
    out = m.profit_loss(period=year, db=db)
    assert out["period"] == year
    assert Decimal(str(out["sales"])) == Decimal("2000"), out


def test_profit_section_on_monthly_sales_page():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(
        os.path.join(root, "frontend", "pages", "monthly-sales.html"), encoding="utf-8"
    ) as fh:
        html = fh.read()
    assert "/reports/profit-loss" in html, "no P&L section calling the endpoint"
    assert 'id="pl-month"' in html, "no month picker for the P&L"
    assert "toggleProfitYear" in html, "no whole-year switch for the P&L"


def test_pivot_filter_and_table_share_one_card():
    # The P&L card once sat between the pivot filters and the pivot table,
    # splitting them. Filter row (ms-year) and result table (ms-head/foot)
    # must read as one block, with P&L after the table.
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(
        os.path.join(root, "frontend", "pages", "monthly-sales.html"), encoding="utf-8"
    ) as fh:
        html = fh.read()
    assert html.find('id="ms-foot"') < html.find('id="pl-month"'), (
        "P&L card still sits between the pivot filters and the pivot table"
    )
