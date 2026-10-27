"""Transport/other charges on purchase invoices: proportional allocation.

The merchant's real cost per kg is purchase rate + GST + its share of the
invoice's transport and other charges. Charges are split across line items
pro-rata to each line's kg and shown per kg and as a landed total-cost
column. They only inform costing: the invoice payable total (what the
supplier is owed, what supplier payments settle) keeps excluding them,
and the total stays overridable to follow the paper invoice.

Run: python -m pytest backend/tests/test_purchase_invoice_charges.py -v
"""

import os
import sys
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _req():
    return SimpleNamespace(headers={}, client=None)


def _supplier(db):
    s = models.Supplier(supplier_id="S-CHG", name="Charge Supplier", contact_number="9000000001")
    db.add(s)
    db.commit()
    return s


def _material(db, code, name):
    mat = models.RawMaterial(material_id=code, name=name, unit="kg", current_stock=Decimal("0"))
    db.add(mat)
    db.commit()
    return mat


def _create(db, items, transport="150", other="300", paid="0", total_amount=None):
    payload = {
        "supplier_id": _supplier(db).id,
        "invoice_date": "2026-10-15T10:00:00",
        "transport_charges": transport,
        "other_charges": other,
        "paid_amount": paid,
        "items": items,
    }
    if total_amount is not None:
        payload["total_amount"] = total_amount
    return m.create_purchase_invoice(payload, _req(), db)


def _two_line_payload(db):
    a = _material(db, "RM-CA", "Charge Seed (Large)")
    b = _material(db, "RM-CB", "Charge Seed (Small)")
    return [
        {"raw_material_id": a.id, "quantity": 100, "rate": 10, "gst_percent": 0},
        {"raw_material_id": b.id, "quantity": 50, "rate": 20, "gst_percent": 0},
    ]


def test_charges_allocate_pro_rata_to_kg(db):
    """100 kg + 50 kg: each charge splits 2/3 and 1/3 â†’ both lines see the
    same per-kg share (1/kg transport, 2/kg other) for 150/300 totals."""
    items = _two_line_payload(db)
    data = _create(db, items)
    it_a, it_b = data["items"]
    assert Decimal(str(it_a["transport_amount"])) == Decimal("100")
    assert Decimal(str(it_a["other_amount"])) == Decimal("200")
    assert Decimal(str(it_b["transport_amount"])) == Decimal("50")
    assert Decimal(str(it_b["other_amount"])) == Decimal("100")


def test_per_kg_cost_includes_gst_and_charges(db):
    items = _two_line_payload(db)
    data = _create(db, items)
    # The create response already carries display dicts with the computed
    # per-kg fields (no second helper round-trip needed).
    d_a, d_b = data["items"]
    # A: 10 + 1.0 transport + 2.0 other = 13; B: 20 + 1.0 + 2.0 = 23.
    assert Decimal(str(d_a["total_cost_per_kg"])) == Decimal("13")
    assert Decimal(str(d_b["total_cost_per_kg"])) == Decimal("23")


def test_payable_total_excludes_charges(db):
    """Goods value only: 100Ã—10 + 50Ã—20 = 2000. Transport is not part of
    what the supplier is owed, so outstanding = total âˆ’ payments."""
    items = _two_line_payload(db)
    data = _create(db, items, paid="500")
    assert Decimal(str(data["total_amount"])) == Decimal("2000")
    assert Decimal(str(data["outstanding_amount"])) == Decimal("1500")
    assert Decimal(str(data["transport_charges"])) == Decimal("150")
    assert Decimal(str(data["other_charges"])) == Decimal("300")


def test_editable_total_follows_the_paper_invoice(db):
    """A typed total wins over the computed one (dues key off it)."""
    items = _two_line_payload(db)
    data = _create(db, items, total_amount="1950")
    assert Decimal(str(data["total_amount"])) == Decimal("1950")
    assert Decimal(str(data["outstanding_amount"])) == Decimal("1950")


def test_per_kg_cost_with_gst_only(db):
    """No charges at all: landed cost is just GST-inclusive rate."""
    a = _material(db, "RM-CG", "Charge GST Seed")
    data = _create(
        db,
        [{"raw_material_id": a.id, "quantity": 100, "rate": 10, "gst_percent": 10}],
        transport="0",
        other="0",
    )
    it = data["items"][0]
    assert Decimal(str(it["amount"])) == Decimal("1100")
    # The create response is already the display dict with the per-kg math.
    assert Decimal(str(it["total_cost_per_kg"])) == Decimal("11")


def test_negative_transport_refused(db):
    items = _two_line_payload(db)
    with pytest.raises(HTTPException) as err:
        _create(db, items, transport="-5")
    assert err.value.status_code == 400
    db.rollback()


def test_detail_view_shows_cost_columns():
    """The saved-invoice detail table carries the per-kg cost columns."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "invoices.html"), encoding="utf-8") as fh:
        html = fh.read()
    i = html.find("window.viewInvoice")
    assert i >= 0
    body = html[i: i + 3000]
    assert "total_cost_per_kg" in body, "detail view missing Total Cost/kg column"
