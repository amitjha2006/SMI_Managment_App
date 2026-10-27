"""Dashboard additions: production_today_kg, open_receivables, material cost trend.

Run: python -m pytest backend/tests/test_dashboard_additions.py
"""

import os
import sys
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _req():
    return SimpleNamespace(headers={}, client=None)


def _customer(db):
    c = models.Customer(customer_id="CUST-D1", name="Dash Co", contact_number="9000000001")
    db.add(c)
    db.commit()
    return c


def test_production_today_counts_only_today(db):
    mat = models.RawMaterial(material_id="RM-D1", name="Dash Raw", unit="kg",
                             current_stock=Decimal("1000"))
    db.add(mat)
    prod = models.Product(product_id="P-D1", name="Dash Masala")
    db.add(prod)
    db.flush()
    size = models.PackagingSize(product_id=prod.id, size_name="250g",
                                weight_in_kg=Decimal("0.25"), is_active=True)
    db.add(size)
    db.commit()
    m.create_production_entry({
        "product_id": prod.id,
        "production_date": datetime.now().isoformat(),
        "target_quantity": 25,
        "outputs": [{"packaging_size_id": size.id, "packets": 100}],
        "consumed_materials": [{"raw_material_id": mat.id, "quantity": 30}],
        "stages": [{"stage": "mixing", "input_quantity": 30, "output_quantity": 25}],
    }, _req(), db)
    stats = m.get_dashboard_stats(db)
    assert stats["production_today_kg"] == 25.0


def test_open_receivables_counts_unpaid_nondrafts(db):
    c = _customer(db)
    for num, total, paid, status in [
        ("CH-D-1", 1000, 0, "confirmed"),
        ("CH-D-2", 1000, 1000, "paid"),
        ("CH-D-3", 500, 0, "draft"),
    ]:
        db.add(models.SalesChallan(
            challan_number=num, challan_date=datetime(2026, 9, 1),
            customer_id=c.id, status=status, total_price=Decimal(str(total)),
            paid_amount=Decimal(str(paid)),
            outstanding_amount=Decimal(str(total - paid))))
    db.commit()
    stats = m.get_dashboard_stats(db)
    assert stats["open_receivables"] == 1


def test_receivables_aging_buckets(db):
    from datetime import timedelta

    c = _customer(db)
    today = datetime.now()
    for num, days_ago in [("CH-A-1", 5), ("CH-A-2", 40), ("CH-A-3", 90)]:
        db.add(models.SalesChallan(
            challan_number=num, challan_date=today - timedelta(days=days_ago),
            customer_id=c.id, status="confirmed", total_price=Decimal("1000"),
            paid_amount=Decimal("0"), outstanding_amount=Decimal("1000")))
    db.commit()
    stats = m.get_dashboard_stats(db)
    assert stats["open_receivables"] == 3
    assert stats["receivables_aging"] == {"d30": 1, "d60": 1, "over60": 1}


def test_sales_trend_carries_material_cost(db):
    mat = models.RawMaterial(material_id="RM-D2", name="Cost Raw", unit="kg",
                             current_stock=Decimal("1000"), purchase_rate=Decimal("50"))
    db.add(mat)
    db.commit()
    db.add(models.RawMaterialStockMovement(
        raw_material_id=mat.id, movement_type="consumption",
        quantity=Decimal("10"), unit="kg", rate=Decimal("50"),
        created_at=datetime.now()))
    db.commit()
    out = m.sales_trend("30d", db)
    assert "material_cost" in out
    assert sum(out["material_cost"]) == 500.0
