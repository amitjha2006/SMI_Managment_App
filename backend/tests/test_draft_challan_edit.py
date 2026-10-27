"""Draft challan edit: header + line changes allowed only in draft.

Run: python -m pytest backend/tests/test_draft_challan_edit.py -v
"""

import os
import sys
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _req():
    return SimpleNamespace(query_params={"reason": "test change note"}, headers={}, client=None)


@pytest.fixture
def setup(db):
    c1 = models.Customer(customer_id="CUST-E1", name="Edit Co", contact_number="9000000011")
    db.add(c1)
    c2 = models.Customer(customer_id="CUST-E2", name="Edit Co 2", contact_number="9000000012")
    db.add(c2)
    prod = models.Product(product_id="P-E1", name="Edit Masala")
    db.add(prod)
    db.flush()
    size = models.PackagingSize(
        product_id=prod.id, size_name="250g",
        weight_in_kg=Decimal("0.25"), rate_per_kg=Decimal("200"), is_active=True,
    )
    db.add(size)
    db.commit()
    return {"c1": c1, "c2": c2, "prod": prod, "size": size}


def _draft(db, setup, **kw):
    payload = {
        "customer_id": setup["c1"].id,
        "challan_date": "2026-09-10T10:00:00",
        "notes": "first",
        "items": [{
            "product_id": setup["prod"].id,
            "packaging_size_id": setup["size"].id,
            "quantity": 4, "weight": 1.0, "rate_per_kg": 200,
        }],
    }
    payload.update(kw)
    return m.create_challan(payload, _req(), db)


def test_update_draft_header(db, setup):
    ch = _draft(db, setup)
    out = m.update_draft_challan(
        ch["id"],
        {"customer_id": setup["c2"].id, "notes": "corrected"},
        _req(), db,
    )
    assert out["customer_id"] == setup["c2"].id
    assert out["notes"] == "corrected"
    # buyer snapshot follows the new customer
    row = db.query(models.SalesChallan).filter(models.SalesChallan.id == ch["id"]).one()
    assert row.customer_id == setup["c2"].id


def test_update_draft_line_qty_and_rate(db, setup):
    ch = _draft(db, setup)
    item_id = ch["items"][0]["id"] if isinstance(ch["items"][0], dict) else ch["items"][0].id
    out = m.update_draft_challan_item(
        ch["id"], item_id, {"quantity": 8, "rate_per_kg": 250}, _req(), db,
    )
    assert out["status"] == "updated"
    row = db.query(models.SalesChallanItem).filter(models.SalesChallanItem.id == item_id).one()
    assert Decimal(str(row.quantity)) == Decimal("8")
    assert Decimal(str(row.rate_per_kg)) == Decimal("250")
    # header totals recomputed: 8 * 0.25kg * 250 = 500
    head = db.query(models.SalesChallan).filter(models.SalesChallan.id == ch["id"]).one()
    assert Decimal(str(head.total_price)) == Decimal("500")
    assert Decimal(str(head.total_weight)) == Decimal("2.000")


def test_update_refused_when_not_draft(db, setup):
    ch = _draft(db, setup)
    db.query(models.SalesChallan).filter(
        models.SalesChallan.id == ch["id"]).one().status = m.ChallanStatus.CONFIRMED
    db.commit()
    with pytest.raises(HTTPException) as e:
        m.update_draft_challan(ch["id"], {"notes": "x"}, _req(), db)
    assert e.value.status_code == 409


def test_update_line_refused_when_not_draft(db, setup):
    ch = _draft(db, setup)
    item_id = ch["items"][0]["id"] if isinstance(ch["items"][0], dict) else ch["items"][0].id
    db.query(models.SalesChallan).filter(
        models.SalesChallan.id == ch["id"]).one().status = m.ChallanStatus.CONFIRMED
    db.commit()
    with pytest.raises(HTTPException) as e:
        m.update_draft_challan_item(ch["id"], item_id, {"quantity": 9}, _req(), db)
    assert e.value.status_code == 409


def test_update_refused_when_dispatched(db, setup):
    from datetime import datetime

    ch = _draft(db, setup)
    row = db.query(models.SalesChallan).filter(models.SalesChallan.id == ch["id"]).one()
    row.dispatched_at = datetime(2026, 9, 11, 10, 0, 0)
    db.commit()
    with pytest.raises(HTTPException) as e:
        m.update_draft_challan(ch["id"], {"notes": "x"}, _req(), db)
    assert e.value.status_code == 409
