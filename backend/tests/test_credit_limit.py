"""Customer credit limits enforced at challan confirm.

Run: python -m pytest backend/tests/test_credit_limit.py
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
from backend.app.core.security import create_access_token  # noqa: E402


def _req(editor=False):
    if not editor:
        return SimpleNamespace(query_params={"reason": "test change note"}, headers={}, client=None)
    tok = create_access_token(data={"sub": "limit_admin", "editor": True})
    return SimpleNamespace(
        query_params={"reason": "limit override"},
        headers={"Authorization": "Bearer " + tok}, client=None,
    )


@pytest.fixture
def setup(db):
    c = models.Customer(customer_id="CUST-L1", name="Limit Co",
                        contact_number="9000000002", credit_limit=Decimal("5000"))
    db.add(c)
    prod = models.Product(product_id="P-L1", name="Limit Masala")
    db.add(prod)
    db.flush()
    size = models.PackagingSize(product_id=prod.id, size_name="250g",
                                weight_in_kg=Decimal("0.25"),
                                rate_per_kg=Decimal("200"), is_active=True)
    db.add(size)
    db.commit()
    # Finished stock so confirm passes the stock guard.
    db.add(models.FinishedProductStockMovement(
        product_id=prod.id, packaging_size_id=size.id,
        movement_type="production", reference_type="test",
        reference_id=0, quantity=Decimal("100")))
    db.commit()
    return {"c": c, "prod": prod, "size": size}


def _challan(db, setup, total):
    rate = Decimal(total) / Decimal("10")
    ch = m.create_challan({
        "customer_id": setup["c"].id,
        "challan_date": "2026-09-10T10:00:00",
        "items": [{"product_id": setup["prod"].id,
                   "packaging_size_id": setup["size"].id,
                   "quantity": 40, "weight": 10,
                   "rate_per_kg": rate}],
    }, _req(), db)
    assert Decimal(str(ch["total_price"])) == Decimal(str(total))
    return ch


def test_confirm_refused_past_limit(db, setup):
    ch = _challan(db, setup, 6000)  # 0 + 6000 > 5000
    with pytest.raises(HTTPException) as e:
        m.set_challan_status(ch["id"], {"status": "confirmed"}, _req(), db)
    assert e.value.status_code == 400
    assert "Credit limit exceeded" in str(e.value.detail)
    assert "1,000.00" in str(e.value.detail)


def test_confirm_allowed_within_limit(db, setup):
    ch = _challan(db, setup, 4000)
    out = m.set_challan_status(ch["id"], {"status": "confirmed"}, _req(), db)
    assert out["status"] in ("confirmed", "CONFIRMED", m.ChallanStatus.CONFIRMED)


def test_zero_limit_means_unlimited(db, setup):
    setup["c"].credit_limit = Decimal("0")
    db.commit()
    ch = _challan(db, setup, 6000)
    out = m.set_challan_status(ch["id"], {"status": "confirmed"}, _req(), db)
    assert str(getattr(out["status"], "value", out["status"])) == "confirmed"


def test_editor_override_allows_and_audits(db, setup):
    ch = _challan(db, setup, 6000)
    out = m.set_challan_status(ch["id"], {"status": "confirmed"}, _req(editor=True), db)
    assert str(getattr(out["status"], "value", out["status"])) == "confirmed"
    notes = [a.notes or "" for a in db.query(models.AuditLog).filter(
        models.AuditLog.module == "sales").all()]
    assert any("Credit limit overridden" in n for n in notes)


def test_migration_lists_credit_limit():
    from pathlib import Path
    text = (
        Path(__file__).resolve().parent.parent / "app" / "core" / "database.py"
    ).read_text(encoding="utf-8")
    assert '("customers", "credit_limit"' in text
