"""Money and unit labels everywhere a number means something.

Every money cell renders through the shared UI.money helper (₹ prefix,
Indian grouping, — for blank) and its header names the unit; quantity
headers name theirs. Bare numbers force the reader to guess the unit.

Run: python -m pytest backend/tests/test_money_units.py -v
"""

import os
import re


def _read(rel):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


def test_shared_money_helper_exists():
    js = _read("frontend/js/components.js")
    assert re.search(r"function money\(", js), "no shared UI.money helper"
    assert "money" in js.split("return {")[-1], "UI.money not exported"


def _money_cells(page, keys):
    html = _read(page)
    return [
        k for k in keys
        if not re.search(rf"{k}:.*window\.UI\.money\(", html)
    ]


def test_challan_money_cells_carry_rupees():
    assert not _money_cells("frontend/pages/challans.html", ["amount", "paid", "outstanding"]), (
        "challan money cells show bare numbers"
    )


def test_expense_money_cells_carry_rupees():
    html = _read("frontend/pages/expenses.html")
    assert re.search(r"amount:\s*window\.UI\.money", html), "expense amount cell is bare"
    assert "UI.money(t)" in html or "UI.money(" in html.split("exp-total")[1][:500], (
        "expense shown total is bare"
    )


def test_purchase_invoice_money_cells_carry_rupees():
    assert not _money_cells(
        "frontend/pages/invoices.html", ["total", "paid", "outstanding"]
    ), "purchase invoice money cells show bare numbers"


def test_payroll_money_cells_carry_rupees():
    html = _read("frontend/pages/payroll.html")
    assert "const money =" not in html and " money =" not in html, (
        "payroll still defines a local bare formatter"
    )
    for cell in ("window.UI.money(a.advance_amount)", "window.UI.money(a.outstanding)",
                 "window.UI.money(d.amount)", "window.UI.money(adv.total_outstanding)"):
        assert cell in html, f"payroll cell not on the shared formatter: {cell}"


def test_money_headers_name_the_unit():
    for page, labels in {
        "frontend/pages/challans.html": ["Paid (₹)", "Balance Due (₹)"],
        "frontend/pages/expenses.html": ["Amount (₹)"],
        "frontend/pages/invoices.html": ["Total (₹)"],
        "frontend/pages/raw-materials.html": ["Rate (₹)"],
        "frontend/pages/monthly-sales.html": ["Weight (kg)"],
    }.items():
        html = _read(page)
        missing = [label for label in labels if label not in html]
        assert not missing, f"{page} headers missing units: {missing}"


def test_challan_form_total_names_rupees():
    html = _read("frontend/pages/challans.html")
    assert re.search(r"Total: ₹", html), "challan form total shows a bare number"
