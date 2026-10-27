"""Cash-flow card: monthly money in/out plus lifetime net.

Received = non-archived customer payments; paid = non-archived supplier
payments + expenses. Month boundaries follow the calendar month, archived
rows never move the figures.

Run: python -m pytest backend/tests/test_cashflow_card.py -v
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


def _month_start():
    now = datetime.now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _setup(db):
    c = models.Customer(customer_id="CUST-F1", name="Flow Co",
                        contact_number="9000000061")
    db.add(c)
    s = models.Supplier(supplier_id="SUP-F1", name="Flow Suppliers",
                        contact_number="9000000062")
    db.add(s)
    db.commit()
    return {"c": c, "s": s}


def _challan(db, customer_id, total=2000):
    prod = models.Product(product_id="P-F1", name="Flow Masala")
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
    ch = m.create_challan({
        "customer_id": customer_id,
        "challan_date": datetime.now().isoformat(),
        "items": [{"product_id": prod.id, "packaging_size_id": size.id,
                   "quantity": 40, "weight": 10,
                   "rate_per_kg": Decimal(total) / Decimal("10")}],
    }, _req(), db)
    m.set_challan_status(ch["id"], {"status": "confirmed"}, _req(), db)
    return db.query(models.SalesChallan).filter(
        models.SalesChallan.id == ch["id"]).one()


def test_month_figures_count_in_and_out(db):
    s = _setup(db)
    ch = _challan(db, s["c"].id)
    now = datetime.now()
    db.add(models.CustomerPayment(
        challan_id=ch.id, payment_date=now, amount=Decimal("500"),
        payment_mode="cash"))
    inv = models.PurchaseInvoice(
        invoice_number="INV-F-1", invoice_date=now, supplier_id=s["s"].id,
        total_amount=Decimal("300"), paid_amount=Decimal("0"),
        outstanding_amount=Decimal("300"))
    db.add(inv)
    db.flush()
    db.add(models.SupplierPayment(
        purchase_invoice_id=inv.id, payment_date=now, amount=Decimal("300"),
        payment_mode="cash"))
    cat = models.ExpenseCategory(name="Flow test", is_active=True)
    db.add(cat)
    db.flush()
    db.add(models.Expense(category_id=cat.id, description="flow",
                          amount=Decimal("100"), date=now, payment_mode="cash"))
    db.commit()
    stats = m.get_dashboard_stats(db)
    flow = stats["cashflow_month"]
    assert Decimal(str(flow["received"])) == Decimal("500"), flow
    assert Decimal(str(flow["paid"])) == Decimal("400"), flow
    assert Decimal(str(stats["cashflow_net"])) == Decimal("100")


def test_archived_and_last_month_rows_are_ignored(db):
    s = _setup(db)
    ch = _challan(db, s["c"].id)
    ms = _month_start()
    import calendar
    prev = 12 if ms.month == 1 else ms.month - 1
    prev_year = ms.year - (1 if ms.month == 1 else 0)
    prev_last = calendar.monthrange(prev_year, prev)[1]
    old = datetime(prev_year, prev, min(prev_last, 15), 10, 0)
    db.add(models.CustomerPayment(
        challan_id=ch.id, payment_date=old, amount=Decimal("700"),
        payment_mode="cash"))
    db.add(models.CustomerPayment(
        challan_id=ch.id, payment_date=datetime.now(), amount=Decimal("50"),
        payment_mode="cash", is_archived=True))
    db.commit()
    stats = m.get_dashboard_stats(db)
    assert Decimal(str(stats["cashflow_month"]["received"])) == Decimal("0"), stats
    assert Decimal(str(stats["cashflow_net"])) == Decimal("700"), stats


def test_dashboard_cash_card_reads_the_new_keys():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "dashboard.html"), encoding="utf-8") as fh:
        dash = fh.read()
    assert 'id="card-cash-flow"' in dash, "no Cash Flow card on the dashboard"
    assert "cashflow_month" in dash, "card does not read monthly figures"
    assert "cashflow_net" in dash, "card does not read the lifetime net"
    assert 'setVisible("card-cash-flow"' in dash, (
        "card ignores the Outstanding-cards visibility preference"
    )
