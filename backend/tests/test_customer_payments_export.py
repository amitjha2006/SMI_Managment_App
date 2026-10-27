"""Customer-payments export: the Export dropdown offers it, so it must work.

The export file's columns must match the import template one-to-one
(Challan Number, Payment Date, Amount, Payment Mode, Transaction Ref,
Payment Notes) so the file round-trips: export -> import into a fresh
database records the payment again.

Run: python -m pytest backend/tests/test_customer_payments_export.py -v
"""

import os
import sys
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402
from backend.app.services.excel_export import build_export, run_export  # noqa: E402
from backend.app.services.excel_import import run_import  # noqa: E402


def _req():
    return SimpleNamespace(query_params={"reason": "test change note"}, headers={}, client=None)


def _setup(db):
    c = models.Customer(customer_id="CUST-X1", name="Export Co",
                        contact_number="9000000031")
    db.add(c)
    prod = models.Product(product_id="P-X1", name="Export Masala")
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


def _confirmed_challan(db, s, total=2000):
    ch = m.create_challan({
        "customer_id": s["c"].id,
        "challan_date": "2026-09-10T10:00:00",
        "items": [{"product_id": s["prod"].id,
                   "packaging_size_id": s["size"].id,
                   "quantity": 40, "weight": 10,
                   "rate_per_kg": Decimal(total) / Decimal("10")}],
    }, _req(), db)
    m.set_challan_status(ch["id"], {"status": "confirmed"}, _req(), db)
    return db.query(models.SalesChallan).filter(
        models.SalesChallan.id == ch["id"]).one()


def _payment(db, challan, amount=500):
    db.add(models.CustomerPayment(
        challan_id=challan.id, payment_date=datetime(2026, 9, 12, 10, 0),
        amount=Decimal(str(amount)), payment_mode="cash",
        transaction_ref="TXN1", notes="Part payment"))
    db.commit()


def test_export_customer_payments_uses_import_columns(db):
    data = build_export(db, "customer_payments")
    assert data["title"] == "Customer Payments"
    assert data["headers"] == [
        "Challan Number", "Payment Date", "Amount",
        "Payment Mode", "Transaction Ref", "Payment Notes",
    ]


def test_export_customer_payments_round_trip(make_db):
    db1, db2 = make_db(), make_db()
    ch1 = _confirmed_challan(db1, _setup(db1))
    _payment(db1, ch1)
    content = run_export(db1, "customer_payments")["content"]

    _confirmed_challan(db2, _setup(db2))
    result = run_import(db2, "customer_payments", content)
    assert result["imported"] == 1, result

    pay = db2.query(models.CustomerPayment).one()
    assert Decimal(str(pay.amount)) == Decimal("500")
    assert pay.transaction_ref == "TXN1"
    got = db2.query(models.SalesChallan).filter(
        models.SalesChallan.challan_number == ch1.challan_number).one()
    assert Decimal(str(got.paid_amount)) == Decimal("500")
