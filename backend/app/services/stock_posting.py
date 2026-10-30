"""Posting of a confirmed sales challan's finished-goods stock movements.

Two flows must post identical movements and must never double-count:

- ``main.set_challan_status`` on the draft -> confirmed transition;
- ``excel_import.import_sales_challans`` — an imported challan is born
  issued (never a draft), so it deducts stock the moment it is created.
  The old importer skipped this, leaving 651 confirmed challans with no
  sales movements and a stock balance fed only by adjustments.

Only the stock movements live here. The customer's outstanding balance is
raised by each caller in its own way (the importer settles part-payments
from the file), so a shared outstanding helper would be wrong.
"""

from decimal import Decimal

from sqlalchemy.orm import Session

from backend.app.models import FinishedProductStockMovement
from backend.app.models.finished_product import FinishedProductMovementType


def challan_stock_posted(db: Session, challan_id: int) -> bool:
    """True when this challan already has SALES movements (idempotency guard)."""
    return (
        db.query(FinishedProductStockMovement)
        .filter(
            FinishedProductStockMovement.reference_type == "sales_challan",
            FinishedProductStockMovement.reference_id == challan_id,
        )
        .count()
        > 0
    )


def post_challan_sales_movements(db: Session, challan, items) -> bool:
    """Create one SALES movement per challan item (deducts finished stock).

    ``items`` are the persisted ``SalesChallanItem`` rows: the caller passes
    the freshly built list during import and ``challan.items`` on confirm,
    because a just-added relationship is not guaranteed to be populated.

    Idempotent: returns False without writing anything when the challan's
    movements already exist, so re-imports and re-confirms never double-count.
    """
    if challan_stock_posted(db, challan.id):
        return False
    for item in items:
        db.add(
            FinishedProductStockMovement(
                product_id=item.product_id,
                packaging_size_id=item.packaging_size_id,
                movement_type=FinishedProductMovementType.SALES,
                reference_type="sales_challan",
                reference_id=challan.id,
                quantity=Decimal(str(item.weight or 0)),  # Positive; balance endpoint deducts SALES qty via stock_kg - out_kg
                rate_per_kg=Decimal(str(item.rate_per_kg or 0)),
                notes=f"Sales challan {challan.challan_number}",
            )
        )
    return True
