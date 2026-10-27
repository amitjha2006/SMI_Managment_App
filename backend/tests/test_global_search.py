"""Global search covers payments and expenses, not just masters.

Receipts/payments are found by their RCPT-/PAY- codes (display codes built
from the row id) or party name, and open the exact row directly. Expenses
are found by description, category or payee, with date/amount/category on
each hit so same-name rows (monthly rent) can be told apart. The
challan_entry profile gets neither group.

Run: python -m pytest backend/tests/test_global_search.py -v
"""

import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _challan(db):
    c = models.Customer(customer_id="C-GS", name="Search Buyer",
                        contact_number="9000000001")
    db.add(c)
    db.commit()
    db.add(
        models.SalesChallan(
            challan_number="CH-GS1", challan_date=datetime(2026, 9, 10, 10, 0, 0),
            customer_id=c.id, status="confirmed",
            total_price=Decimal("1000"), paid_amount=Decimal("0"),
            outstanding_amount=Decimal("1000"),
        )
    )
    db.commit()
    return db.query(models.SalesChallan).filter(
        models.SalesChallan.challan_number == "CH-GS1").one()


def _receipt(db):
    ch = _challan(db)
    db.add(
        models.CustomerPayment(
            challan_id=ch.id, payment_date=datetime(2026, 9, 11, 10, 0, 0),
            amount=Decimal("400"), payment_mode="cash",
        )
    )
    db.commit()
    return db.query(models.CustomerPayment).order_by(
        models.CustomerPayment.id.desc()).first()


def _payment(db):
    s = models.Supplier(supplier_id="S-GS", name="Search Supplier",
                        contact_number="9000000002")
    db.add(s)
    db.commit()
    inv = models.PurchaseInvoice(
        invoice_number="PUR-GS1", invoice_date=datetime(2026, 9, 10, 10, 0, 0),
        supplier_id=s.id, total_amount=Decimal("800"),
        paid_amount=Decimal("0"), outstanding_amount=Decimal("800"),
    )
    db.add(inv)
    db.commit()
    db.add(
        models.SupplierPayment(
            purchase_invoice_id=inv.id, payment_date=datetime(2026, 9, 11, 10, 0, 0),
            amount=Decimal("300"), payment_mode="upi",
        )
    )
    db.commit()
    return db.query(models.SupplierPayment).order_by(
        models.SupplierPayment.id.desc()).first()


def _expense(db, description, amount="12000", days_ago=5):
    cat = db.query(models.ExpenseCategory).filter(
        models.ExpenseCategory.name == "Rent").first()
    if not cat:
        cat = models.ExpenseCategory(name="Rent", is_active=True)
        db.add(cat)
        db.flush()
    db.add(
        models.Expense(
            category_id=cat.id, description=description,
            amount=Decimal(amount),
            date=datetime.now() - timedelta(days=days_ago),
            payment_mode="cash", payee="Sharma",
        )
    )
    db.commit()


def test_receipt_found_by_code(db):
    pay = _receipt(db)
    out = m.search_all(db, f"RCPT-{pay.id:04d}")
    assert len(out["payments"]) == 1, out
    hit = out["payments"][0]
    assert hit["kind"] == "receipt", hit
    assert hit["challan_id"] == pay.challan_id, hit


def test_payment_found_by_code(db):
    pay = _payment(db)
    out = m.search_all(db, f"PAY-{pay.id:04d}")
    assert len(out["payments"]) == 1, out
    hit = out["payments"][0]
    assert hit["kind"] == "payment", hit
    assert hit["invoice_id"] == pay.purchase_invoice_id, hit


def test_expense_found_by_description(db):
    _expense(db, "Factory rent")
    out = m.search_all(db, "factory rent")
    assert len(out["expenses"]) == 1, out
    hit = out["expenses"][0]
    assert hit["description"] == "Factory rent", hit
    assert hit["category"] == "Rent", hit


def test_same_name_expenses_carry_telling_fields(db):
    _expense(db, "Factory rent", amount="12000", days_ago=35)
    _expense(db, "Factory rent", amount="12500", days_ago=5)
    out = m.search_all(db, "factory rent")
    assert len(out["expenses"]) == 2, out
    for hit in out["expenses"]:
        assert hit["date"] and hit["amount"] and hit["category"], hit
    assert out["expenses"][0]["amount"] != out["expenses"][1]["amount"], out


def test_new_groups_are_capped(db):
    for i in range(10):
        _expense(db, f"Capped expense {i}")
    out = m.search_all(db, "capped expense")
    assert len(out["expenses"]) <= 8, out


def test_restricted_profile_gets_neither_group(db):
    _receipt(db)
    _expense(db, "Factory rent")
    out = m.search_all(db, "a", include_payments=False, include_expenses=False)
    assert out["payments"] == [] and out["expenses"] == [], out


def test_blank_query_returns_nothing_new(db):
    _receipt(db)
    _expense(db, "Factory rent")
    out = m.search_all(db, "   ")
    assert out["payments"] == [] and out["expenses"] == [], out


def _read(rel):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


def test_search_panel_shows_payments_and_expenses():
    js = _read("frontend/js/app.js")
    assert '"Payments"' in js and '"Expenses"' in js, (
        "global search renders no payment/expense groups"
    )
    assert "#/challans?challan=" in js and "#/invoices?invoice=" in js, (
        "payment hits do not deep-link to the parent record"
    )


def test_expense_hits_link_to_filtered_expenses():
    assert 'goLink("expenses"' in _read("frontend/js/app.js"), (
        "expense hits do not jump to the Expenses page"
    )
    assert 'applySearchDeepLink("exp-search")' in _read("frontend/pages/expenses.html"), (
        "Expenses page ignores the ?search= deep link"
    )


def test_payment_rows_open_highlighted():
    for page, view in (("challans", "viewChallan"), ("invoices", "viewInvoice")):
        html = _read(f"frontend/pages/{page}.html")
        assert "data-pay-id" in html, f"{page} payment rows carry no id to highlight"
        assert "?payment=" in html or "&payment=" in html, (
            f"{page} never opens a payment directly from a link"
        )
        assert view in html and "highlightPayId" in html, (
            f"{view} cannot land on the exact row"
        )


def test_help_covers_search_and_recent():
    html = _read("frontend/pages/help.html")
    assert "RCPT-" in html and "PAY-" in html, "help never mentions payment code search"
    assert "Recent Transactions" in html, "help has no Recent Transactions section"
    assert "description" in html.lower(), "help never says what expense reference shows"
