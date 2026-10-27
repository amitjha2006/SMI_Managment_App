"""The finished-goods balance must not list a merged duplicate pack size.

A deactivated duplicate ("100g" twice for Cumin Powder, size 37) kept
appearing on the dashboard next to its active twin. Deactivated sizes that
still hold stock stay visible on purpose (pinned by
test_finished_balance.py), so the fix is narrower: a deactivated size with
NO movements of its own - i.e. one already merged into its twin - must not
produce an empty lookalike row.

Run: python -m pytest backend/tests/test_finished_stock_balance.py
"""

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402
from backend.app.models.finished_product import (  # noqa: E402
    FinishedProductMovementType,
)


def _product_with_two_sizes(db):
    product = models.Product(product_id="PROD-1", name="Cumin Powder", is_active=True)
    db.add(product)
    db.flush()
    active = models.PackagingSize(
        product_id=product.id,
        size_name="100g",
        weight_in_kg=Decimal("0.1"),
        rate_per_kg=Decimal("100"),
        packets_per_unit=10,
        is_active=True,
    )
    # The old duplicate: same name, deactivated, merged into its twin (no
    # movements of its own left).
    inactive = models.PackagingSize(
        product_id=product.id,
        size_name="100g",
        weight_in_kg=Decimal("0.1"),
        rate_per_kg=Decimal("100"),
        packets_per_unit=10,
        is_active=False,
    )
    db.add_all([active, inactive])
    db.flush()
    return product, active, inactive


def _add_movement(db, product_id, size_id, mtype, kg):
    db.add(
        models.FinishedProductStockMovement(
            product_id=product_id,
            packaging_size_id=size_id,
            movement_type=mtype,
            reference_type="manual_adjustment",
            quantity=Decimal(str(kg)),
            rate_per_kg=Decimal("100"),
            notes="Old Stock",
        )
    )


def test_merged_inactive_duplicate_without_history_is_hidden(db):
    product, active, inactive = _product_with_two_sizes(db)
    _add_movement(db, product.id, active.id, FinishedProductMovementType.ADJUSTMENT, 5.7)
    db.commit()

    data = m.finished_stock_balance(db)

    shown = [
        (r["size_id"], r["size_name"])
        for r in data["rows"]
        if r["product_id"] == product.id
    ]
    assert shown == [(active.id, "100g")], shown


def test_inactive_duplicate_with_its_own_history_still_shows(db):
    """A deactivated size that still holds stock stays visible by design."""
    product, active, inactive = _product_with_two_sizes(db)
    _add_movement(db, product.id, inactive.id, FinishedProductMovementType.PRODUCTION, 5.0)
    db.commit()

    data = m.finished_stock_balance(db)

    shown = sorted(
        r["size_id"] for r in data["rows"] if r["product_id"] == product.id
    )
    assert shown == sorted([active.id, inactive.id]), shown
