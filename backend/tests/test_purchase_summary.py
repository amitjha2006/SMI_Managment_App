"""Purchase summary: per-material confirmed purchases in range.

One row per active material (Sr No is client-side): purchased kg plus
total purchase amount, zeros included. Only confirmed, non-archived
invoices inside the range count; drafts, archived and out-of-range rows
never move the totals.

Run: python -m pytest backend/tests/test_purchase_summary.py -v
"""

import os
import sys
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _mat(db, code, name):
    mat = models.RawMaterial(
        material_id=code, name=name, unit="kg",
        opening_stock=Decimal("0"), current_stock=Decimal("0"),
    )
    db.add(mat)
    db.commit()
    return mat


def _inv(db, sup, number, day, confirmed=True, archived=False):
    inv = models.PurchaseInvoice(
        invoice_number=number,
        invoice_date=datetime(2026, 9, day, 10, 0, 0),
        supplier_id=sup.id, total_amount=Decimal("100"),
        paid_amount=Decimal("0"), outstanding_amount=Decimal("100"),
        is_confirmed=confirmed, is_archived=archived,
    )
    db.add(inv)
    db.commit()
    return inv


def _item(db, inv, mat, qty, rate):
    db.add(models.PurchaseInvoiceItem(
        purchase_invoice_id=inv.id, raw_material_id=mat.id,
        quantity=Decimal(str(qty)), unit="kg",
        rate=Decimal(str(rate)), amount=Decimal(str(qty * rate)),
    ))
    db.commit()


def _seed(db):
    sup = models.Supplier(supplier_id="S-PS", name="PS Supplier", contact_number="9000000001")
    db.add(sup)
    db.commit()
    a = _mat(db, "RM-PSA", "PS Turmeric")
    b = _mat(db, "RM-PSB", "PS Coriander")
    _mat(db, "RM-PSC", "PS Idle")
    inv = _inv(db, sup, "PS-1", 5)
    _item(db, inv, a, 100, 120)
    _item(db, inv, b, 50, 90)
    draft = _inv(db, sup, "PS-DRAFT", 6, confirmed=False)
    _item(db, draft, a, 999, 999)
    gone = _inv(db, sup, "PS-ARCH", 7, archived=True)
    _item(db, gone, b, 111, 111)
    return a, b


def test_confirmed_in_range_only(db):
    a, b = _seed(db)
    out = m.purchase_summary(db, "2026-09-01", "2026-09-30")
    rows = {r["name"]: r for r in out["rows"]}
    assert rows["PS Turmeric"]["purchased_kg"] == 100
    assert rows["PS Turmeric"]["purchase_amount"] == 12000
    assert rows["PS Coriander"]["purchased_kg"] == 50
    assert rows["PS Coriander"]["purchase_amount"] == 4500
    assert rows["PS Idle"]["purchased_kg"] == 0
    assert rows["PS Idle"]["purchase_amount"] == 0
    assert out["totals"] == {"materials": 3, "purchased_kg": 150, "purchase_amount": 16500}


def test_out_of_range_excluded(db):
    sup = models.Supplier(supplier_id="S-PS2", name="PS2", contact_number="9000000002")
    db.add(sup)
    db.commit()
    a = _mat(db, "RM-PSX", "PS Aug")
    inv = models.PurchaseInvoice(
        invoice_number="PS-AUG", invoice_date=datetime(2026, 8, 5, 10, 0, 0),
        supplier_id=sup.id, total_amount=Decimal("10"), paid_amount=Decimal("0"),
        outstanding_amount=Decimal("10"), is_confirmed=True, is_archived=False,
    )
    db.add(inv)
    db.commit()
    _item(db, inv, a, 10, 10)
    out = m.purchase_summary(db, "2026-09-01", "2026-09-30")
    assert out["rows"][0]["purchased_kg"] == 0


def test_purchase_summary_page_pins():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "js", "app.js"), encoding="utf-8") as fh:
        assert "purchase-summary.html" in fh.read(), "no purchase summary route"
    with open(os.path.join(root, "frontend", "index.html"), encoding="utf-8") as fh:
        index = fh.read()
    ai = index.find('href="#/analysis"')
    pi = index.find('href="#/purchase-summary"')
    assert ai >= 0 and pi >= 0 and ai < pi, "not next to Stock Analysis"
    path = os.path.join(root, "frontend", "pages", "purchase-summary.html")
    with open(path, encoding="utf-8") as fh:
        html = fh.read()
    for token in ("/reports/purchase-summary", "bar-qty", "bar-amt", "pusumPreset"):
        assert token in html, f"page misses {token}"
    i = html.find("window.pusumReset")
    assert i >= 0
    body = html[i:i + 600]
    assert 'getElementById("rep-pu-from").value = ""' in body, "Reset keeps a From date"
    assert 'getElementById("rep-pu-to").value = ""' in body, "Reset keeps a To date"
    assert "loadPurchaseSummary()" in body, "Reset does not reload"
    i = html.find(".bar-cell {")
    assert i >= 0 and "block" in html[i:i + 120], (
        "bar cell is inline: fills span the text, not the cell"
    )
    j = html.find(".bar-cell .bar-fill {")
    assert j >= 0 and "top: 0" in html[j:j + 160], (
        "bars are inset pills instead of even full-cell bands"
    )
