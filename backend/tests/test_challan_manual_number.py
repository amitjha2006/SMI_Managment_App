"""Manual challan numbers: typed number wins, blank stays automatic.

An optional "Challan No" on the create form lets the shop keep paper-book
numbers. A typed number must be unique (any status); blank keeps the
FY auto-sequence, which a manual number never consumes. The number is
frozen at creation like everything else on the document.

Run: python -m pytest backend/tests/test_challan_manual_number.py -v
"""

import os
import sys
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _req():
    return SimpleNamespace(headers={}, client=None)


def _setup(db):
    c = models.Customer(customer_id="CUST-MN", name="Manual Co", contact_number="9000000022")
    db.add(c)
    prod = models.Product(product_id="P-MN", name="Manual Masala")
    db.add(prod)
    db.flush()
    size = models.PackagingSize(
        product_id=prod.id, size_name="250g",
        weight_in_kg=Decimal("0.25"), rate_per_kg=Decimal("200"), is_active=True,
    )
    db.add(size)
    db.commit()
    return {"c": c, "prod": prod, "size": size}


def _payload(s, **extra):
    body = {
        "customer_id": s["c"].id,
        "challan_date": "2026-09-10T00:00:00",
        "items": [{
            "product_id": s["prod"].id,
            "packaging_size_id": s["size"].id,
            "quantity": 4, "weight": 1.0, "rate_per_kg": 200,
        }],
    }
    body.update(extra)
    return body


def test_typed_number_is_used(db):
    s = _setup(db)
    d = m.create_challan(_payload(s, challan_number="  BOOK-101 "), _req(), db)
    assert d["challan_number"] == "BOOK-101", d


def test_duplicate_number_is_rejected(db):
    s = _setup(db)
    m.create_challan(_payload(s, challan_number="BOOK-101"), _req(), db)
    with pytest.raises(HTTPException) as e:
        m.create_challan(_payload(s, challan_number="BOOK-101"), _req(), db)
    assert e.value.status_code == 400


def test_duplicate_check_ignores_case(db):
    s = _setup(db)
    m.create_challan(_payload(s, challan_number="BOOK-101"), _req(), db)
    with pytest.raises(HTTPException) as e:
        m.create_challan(_payload(s, challan_number="book-101"), _req(), db)
    assert e.value.status_code == 400
    assert "already exists" in e.value.detail


def test_blank_stays_automatic_and_manual_skips_sequence(db):
    s = _setup(db)
    m.create_challan(_payload(s, challan_number="BOOK-101"), _req(), db)
    auto = m.create_challan(_payload(s), _req(), db)
    assert auto["challan_number"].startswith("CH/"), auto
    assert auto["challan_number"].endswith("/0001"), auto
