"""Permanent delete of draft challans refuses cleanly instead of crashing.

A draft is only deletable while nothing was raised from it: payments, stock
postings, and the sales invoice each block the delete with a 409 that names
the reason. The invoice case once fell through every guard and died in the
commit with an IntegrityError (FK from sales_invoices.challan_id), which the
UI could only report as a bare "Delete failed".

Run: python -m pytest backend/tests/test_challan_permanent_delete.py -v
"""

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _req():
    return SimpleNamespace(query_params={"reason": "test change note"}, headers={}, client=None)


@pytest.fixture
def setup(db):
    c1 = models.Customer(customer_id="CUST-D1", name="Del Co", contact_number="9000000071")
    db.add(c1)
    prod = models.Product(product_id="P-D1", name="Del Masala")
    db.add(prod)
    db.flush()
    size = models.PackagingSize(
        product_id=prod.id, size_name="250g",
        weight_in_kg=Decimal("0.25"), rate_per_kg=Decimal("200"), is_active=True,
    )
    db.add(size)
    db.commit()
    return {"c1": c1, "prod": prod, "size": size}


def _draft(db, setup):
    return m.create_challan({
        "customer_id": setup["c1"].id,
        "challan_date": "2026-09-10T10:00:00",
        "items": [{
            "product_id": setup["prod"].id,
            "packaging_size_id": setup["size"].id,
            "quantity": 4, "weight": 1.0, "rate_per_kg": 200,
        }],
    }, _req(), db)


def test_plain_draft_deletes_with_its_items(db, setup):
    ch = _draft(db, setup)
    out = m.delete_challan_permanently(ch["id"], _req(), db)
    assert out["status"] == "deleted"
    assert out["items_removed"] == 1
    assert db.query(models.SalesChallan).filter(
        models.SalesChallan.id == ch["id"]).count() == 0
    assert db.query(models.SalesChallanItem).filter(
        models.SalesChallanItem.challan_id == ch["id"]).count() == 0


def test_draft_with_invoice_is_refused_not_crashed(db, setup):
    ch = _draft(db, setup)
    db.add(models.SalesInvoice(
        invoice_number="INV-D-1", invoice_date=datetime(2026, 9, 28),
        challan_id=ch["id"], customer_id=setup["c1"].id, intra_state=True,
        taxable_value=Decimal("5714.29"), grand_total=Decimal("6000"),
    ))
    db.commit()
    with pytest.raises(HTTPException) as exc:
        m.delete_challan_permanently(ch["id"], _req(), db)
    assert exc.value.status_code == 409
    assert "INV-D-1" in exc.value.detail
    # Nothing was half-deleted.
    assert db.query(models.SalesChallan).filter(
        models.SalesChallan.id == ch["id"]).count() == 1


def test_non_draft_is_refused(db, setup):
    ch = _draft(db, setup)
    db.query(models.SalesChallan).filter(
        models.SalesChallan.id == ch["id"]).one().status = m.ChallanStatus.CONFIRMED
    db.commit()
    with pytest.raises(HTTPException) as exc:
        m.delete_challan_permanently(ch["id"], _req(), db)
    assert exc.value.status_code == 409
