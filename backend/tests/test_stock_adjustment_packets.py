"""Packet-based entry for finished-goods stock adjustments.

The adjustment form made the operator multiply out packets by hand even
after a packet size was picked: 10 packets of 100 g had to be typed as
1 kg. Packet entry converts packets x the size's own weight into the kg
the finished-stock ledger stores, and the exact packet count lands on
the movement row so history shows both.

Run: python -m pytest backend/tests/test_stock_adjustment_packets.py -v
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


def _finished_product(db, size_name="100g", weight_kg=Decimal("0.100")):
    prod = models.Product(product_id="P-PKT", name="Packet Test Masala")
    db.add(prod)
    db.flush()
    size = models.PackagingSize(
        product_id=prod.id,
        size_name=size_name,
        weight_in_kg=weight_kg,
        rate_per_kg=Decimal("200"),
        packets_per_unit=1,
        is_active=True,
    )
    db.add(size)
    db.commit()
    return prod, size


def _movement(db, prod_id):
    return (
        db.query(models.FinishedProductStockMovement)
        .filter(models.FinishedProductStockMovement.product_id == prod_id)
        .order_by(models.FinishedProductStockMovement.id.desc())
        .first()
    )


def _adjust(db, prod, size, packets, reason="recount"):
    out = m.create_stock_adjustment(
        {
            "kind": "finished_product",
            "product_id": prod.id,
            "packaging_size_id": size.id,
            "quantity": packets,
            "entry_unit": "packets",
            "reason": reason,
        },
        _req(),
        db,
    )
    assert out["status"] == "recorded", out
    return _movement(db, prod.id)


def test_packet_entry_converts_to_kg(db):
    """10 packets of a 100 g size must land as exactly 1.000 kg."""
    prod, size = _finished_product(db)
    mv = _adjust(db, prod, size, 10)
    assert mv.quantity == Decimal("1.000")
    assert mv.packets == 10
    assert "10 pkt = 1 kg" in (mv.notes or "")


def test_packet_entry_negative_removes_kg(db):
    """-3 packets of a 100 g size removes 0.300 kg from the ledger."""
    prod, size = _finished_product(db)
    mv = _adjust(db, prod, size, -3, reason="damage")
    assert mv.quantity == Decimal("-0.300")
    assert mv.packets == -3
    assert "-3 pkt = -0.3 kg" in (mv.notes or "")


def test_packet_entry_fractional_locked_out(db):
    """Packets are whole: fractional bins belong to kg mode."""
    prod, size = _finished_product(db)
    with pytest.raises(HTTPException) as err:
        _adjust(db, prod, size, 2.5)
    assert err.value.status_code == 400
    db.rollback()


def test_packet_entry_zero_weight_size_refused(db):
    """A size without a packet weight cannot convert — point at the fix."""
    prod, size = _finished_product(db, weight_kg=Decimal("0"))
    with pytest.raises(HTTPException) as err:
        _adjust(db, prod, size, 5)
    assert err.value.status_code == 400
    assert "packet weight" in err.value.detail
    db.rollback()


def test_kg_entry_still_works_without_entry_unit(db):
    """Legacy payloads (no entry_unit) keep today's kg semantics verbatim."""
    prod, size = _finished_product(db)
    out = m.create_stock_adjustment(
        {
            "kind": "finished_product",
            "product_id": prod.id,
            "packaging_size_id": size.id,
            "quantity": 0.25,
            "reason": "recount",
        },
        _req(),
        db,
    )
    assert out["status"] == "recorded", out
    mv = _movement(db, prod.id)
    assert mv.quantity == Decimal("0.25")


def test_packet_entry_rejected_for_raw_materials(db):
    """Packet counts apply to finished goods only; raw stock is kg/NOS."""
    mat = models.RawMaterial(
        material_id="RM-PKT", name="Raw Packet Test", unit="kg",
        current_stock=Decimal("100"),
    )
    db.add(mat)
    db.commit()
    with pytest.raises(HTTPException) as err:
        m.create_stock_adjustment(
            {
                "kind": "raw_material",
                "raw_material_id": mat.id,
                "quantity": 5,
                "entry_unit": "packets",
                "reason": "recount",
            },
            _req(),
            db,
        )
    assert err.value.status_code == 400
    assert "finished goods" in err.value.detail
    db.rollback()
