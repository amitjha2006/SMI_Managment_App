"""Purchase invoice items carry the material name, not just its id.

The detail view's Material column rendered raw_material_id, so every line
showed a bare number. Both the list and detail endpoints now resolve
material_name per line (same precedent as supplier_name), and the view
prefers it with the id as fallback.

Run: python -m pytest backend/tests/test_purchase_item_names.py -v
"""

import os
import sys
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _invoice(db):
    s = models.Supplier(supplier_id="S-PIN", name="Pin Supplier",
                        contact_number="9000000001")
    db.add(s)
    db.commit()
    mat = models.RawMaterial(material_id="RM-PIN", name="Pin Haldi",
                             unit="kg", current_stock=Decimal("0"))
    db.add(mat)
    db.commit()
    inv = models.PurchaseInvoice(
        invoice_number="PUR-PIN1", invoice_date=datetime(2026, 9, 10, 10, 0, 0),
        supplier_id=s.id, total_amount=Decimal("500"),
        paid_amount=Decimal("0"), outstanding_amount=Decimal("500"),
    )
    db.add(inv)
    db.commit()
    db.add(
        models.PurchaseInvoiceItem(
            purchase_invoice_id=inv.id, raw_material_id=mat.id,
            quantity=Decimal("10"), unit="kg", rate=Decimal("50"),
            amount=Decimal("500"),
        )
    )
    db.commit()
    return inv.id


def test_detail_items_carry_material_name(db):
    out = m.get_purchase_invoice(_invoice(db), db)
    assert out["items"], out
    assert out["items"][0]["material_name"] == "Pin Haldi", out["items"]


def test_list_items_carry_material_name(db):
    inv_id = _invoice(db)
    rows = m.list_purchase_invoices(db=db)
    row = next(r for r in rows if r["id"] == inv_id)
    assert row["items"][0]["material_name"] == "Pin Haldi", row["items"]


def test_detail_view_renders_name_not_id():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "invoices.html"), encoding="utf-8") as fh:
        html = fh.read()
    i = html.find("window.viewInvoice")
    assert i >= 0
    body = html[i:i + 2000]
    assert "material_name" in body, "detail Material column still renders the raw id"
