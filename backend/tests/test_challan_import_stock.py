"""Imported sales challans must reduce finished-goods stock.

The old importer created challans already confirmed but never posted the
SALES stock movements, so packets added through Stock Adjustments were
never reduced by imported (already-sold) challans - the laptop database
had 651 confirmed challans and zero sales movements.

Run: python -m pytest backend/tests/test_challan_import_stock.py
"""

import io
import os
import sys
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app import models  # noqa: E402
from backend.app.models.finished_product import (  # noqa: E402
    FinishedProductMovementType,
)
from backend.app.services.excel_import import run_import  # noqa: E402

_seq = iter(range(1, 10000))


def _customer(db):
    c = models.Customer(
        customer_id=f"CUST-{next(_seq):04d}",
        name="Stock Test Traders",
        contact_number="9876500001",
    )
    db.add(c)
    db.commit()
    return c


def _product_with_pack(db, name="Turmeric Powder", size="250g", weight="0.25"):
    p = models.Product(product_id=f"PROD-{next(_seq):04d}", name=name, is_active=True)
    db.add(p)
    db.flush()
    ps = models.PackagingSize(
        product_id=p.id,
        size_name=size,
        weight_in_kg=Decimal(weight),
        rate_per_kg=Decimal("100"),
        packets_per_unit=4,
        is_active=True,
    )
    db.add(ps)
    db.commit()
    return p, ps


def _xlsx(rows):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _challan_rows(number="CH-STOCK-1", date="2026-07-01", qty=120):
    return [
        ["Challan Number", "Challan Date", "Customer Name", "Product Name",
         "Packaging Size", "Quantity", "Rate per Kg", "Paid Amount"],
        [number, date, "Stock Test Traders", "Turmeric Powder",
         "250g", qty, 100, 0],
    ]


def _sales_movements(db):
    return (
        db.query(models.FinishedProductStockMovement)
        .filter(models.FinishedProductStockMovement.reference_type == "sales_challan")
        .all()
    )


def _balance_kg(db, product_id, packaging_size_id):
    """Signed finished-stock balance, same sign rule as /finished-stock."""
    total = Decimal("0")
    for m in (
        db.query(models.FinishedProductStockMovement)
        .filter_by(product_id=product_id, packaging_size_id=packaging_size_id)
        .all()
    ):
        sign = (
            Decimal("-1")
            if m.movement_type
            in (FinishedProductMovementType.SALES, FinishedProductMovementType.WASTAGE)
            else Decimal("1")
        )
        total += sign * Decimal(str(m.quantity or 0))
    return total


def test_imported_challan_reduces_adjusted_stock(db, make_db):
    _ = make_db
    _customer(db)
    p, ps = _product_with_pack(db)
    # Opening stock added through Stock Adjustments, like the laptop data.
    db.add(
        models.FinishedProductStockMovement(
            product_id=p.id,
            packaging_size_id=ps.id,
            movement_type=FinishedProductMovementType.ADJUSTMENT,
            reference_type="manual_adjustment",
            quantity=Decimal("100"),
            rate_per_kg=Decimal("100"),
            notes="Old Stock (+100 kg)",
        )
    )
    db.commit()

    out = run_import(db, "sales_challans", _xlsx(_challan_rows()))
    assert out["imported"] == 1, out

    # 120 packets x 0.25 kg = 30 kg sold -> 100 - 30 = 70 kg must remain.
    assert _balance_kg(db, p.id, ps.id) == Decimal("70")
    ch = (
        db.query(models.SalesChallan)
        .filter(models.SalesChallan.challan_number == "CH-STOCK-1")
        .first()
    )
    moves = [m for m in _sales_movements(db) if m.reference_id == ch.id]
    assert len(moves) == 1, moves
    assert str(moves[0].movement_type.value) == "sales"
    assert Decimal(str(moves[0].quantity)) == Decimal("30")


def test_reimport_does_not_double_post(db, make_db):
    _ = make_db
    _customer(db)
    _product_with_pack(db)
    rows = _challan_rows()

    assert run_import(db, "sales_challans", _xlsx(rows))["imported"] == 1
    assert len(_sales_movements(db)) == 1

    out2 = run_import(db, "sales_challans", _xlsx(rows))
    assert out2["imported"] == 0
    assert len(_sales_movements(db)) == 1


def test_reimport_backfills_unposted_existing_challan(db, make_db):
    _ = make_db
    c = _customer(db)
    p, ps = _product_with_pack(db)
    # A challan the OLD importer left behind: confirmed, no stock movements.
    ch = models.SalesChallan(
        challan_number="CH-OLD-1",
        challan_date=datetime(2026, 7, 1),
        customer_id=c.id,
        status="confirmed",
        total_weight=Decimal("30"),
        total_price=Decimal("3000"),
        paid_amount=Decimal("0"),
        outstanding_amount=Decimal("3000"),
    )
    db.add(ch)
    db.flush()
    db.add(
        models.SalesChallanItem(
            challan_id=ch.id,
            product_id=p.id,
            packaging_size_id=ps.id,
            quantity=Decimal("120"),
            weight=Decimal("30"),
            rate_per_kg=Decimal("100"),
            amount=Decimal("3000"),
            product_name=p.name,
            size_name=ps.size_name,
        )
    )
    db.commit()
    assert _sales_movements(db) == []  # the bug state this reproduces

    run_import(db, "sales_challans", _xlsx(_challan_rows(number="CH-OLD-1")))

    moves = _sales_movements(db)
    assert len(moves) == 1
    assert Decimal(str(moves[0].quantity)) == Decimal("30")
    # No opening stock was entered, so the honest balance is negative.
    assert _balance_kg(db, p.id, ps.id) == Decimal("-30")
