"""Customer statement: per-customer dues with ageing, ready to share.

GET /customers/{id}/statement returns the customer header plus every open
(non-draft, outstanding) challan with its age bucket, totals and an ageing
summary. Unknown customers 404. Paid-off and draft-only customers report
zeros with an empty list.

Run: python -m pytest backend/tests/test_customer_statement.py -v
"""

import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _req():
    return SimpleNamespace(query_params={"reason": "test change note"}, headers={}, client=None)


def _setup(db):
    c = models.Customer(customer_id="CUST-S1", name="Statement Co",
                        contact_number="9000000081")
    db.add(c)
    prod = models.Product(product_id="P-S1", name="Statement Masala")
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
    return {"c": c, "prod": prod, "size": size}


def _confirmed(db, s, date_iso, total=2000):
    ch = m.create_challan({
        "customer_id": s["c"].id,
        "challan_date": date_iso,
        "items": [{"product_id": s["prod"].id,
                   "packaging_size_id": s["size"].id,
                   "quantity": 40, "weight": 10,
                   "rate_per_kg": Decimal(total) / Decimal("10")}],
    }, _req(), db)
    m.set_challan_status(ch["id"], {"status": "confirmed"}, _req(), db)
    return ch


def test_statement_lists_open_dues_with_ageing(db):
    s = _setup(db)
    old = (datetime.now() - timedelta(days=75)).isoformat()
    young = datetime.now().isoformat()
    _confirmed(db, s, old)
    _confirmed(db, s, young)
    st = m.customer_statement(s["c"].id, db)
    assert st["customer_name"] == "Statement Co"
    assert st["contact_number"] == "9000000081"
    assert len(st["challans"]) == 2, st
    assert Decimal(str(st["total_outstanding"])) == Decimal("4000"), st
    assert st["aging"]["over60"] == 1, st
    assert st["aging"]["d30"] == 1, st
    old_row = [c for c in st["challans"] if c["age_days"] > 30][0]
    assert Decimal(str(old_row["outstanding"])) == Decimal("2000")


def test_paid_and_draft_challans_stay_out(db):
    s = _setup(db)
    ch = _confirmed(db, s, datetime.now().isoformat())
    row = db.query(models.SalesChallan).filter(
        models.SalesChallan.id == ch["id"]).one()
    m.create_payment({"challan_id": row.id, "amount": 2000,
                      "payment_date": datetime.now().isoformat(),
                      "payment_mode": "cash"}, _req(), db)
    m.create_challan({
        "customer_id": s["c"].id,
        "challan_date": datetime.now().isoformat(),
        "items": [{"product_id": s["prod"].id,
                   "packaging_size_id": s["size"].id,
                   "quantity": 4, "weight": 1.0, "rate_per_kg": 200}],
    }, _req(), db)
    st = m.customer_statement(s["c"].id, db)
    assert st["challans"] == [], st
    assert Decimal(str(st["total_outstanding"])) == Decimal("0"), st


def test_unknown_customer_404s(db):
    with pytest.raises(HTTPException) as e:
        m.customer_statement(999999, db)
    assert e.value.status_code == 404


def test_detail_view_renders_statement_and_share():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "customers.html"), encoding="utf-8") as fh:
        html = fh.read()
    assert "loadCustomerStatement" in html, "detail view never loads the dues"
    assert "/customers/" in html and "/statement" in html
    assert "shareStatement" in html, "no WhatsApp follow-up on the statement"
    assert "wa.me" in html
