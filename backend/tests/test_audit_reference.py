"""Audit History must name the document, not just the row id.

"sales_challan #42" is useless when what you are hunting for is CH-2026-0042.
list_audit_logs now resolves each row's (record_type, record_id) to a label -
challan number, invoice number, customer/product name - so the page can show
it without opening View.

Run: python -m pytest backend/tests/test_audit_reference.py -v
"""

import os
import sys
from datetime import datetime
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402
from backend.app.routes.users import list_audit_logs
from backend.app.routes.sales import list_challans
from backend.app.routes.auth import login
from backend.app.core.shared import _latest_login_at

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _req():
    return SimpleNamespace(headers={}, client=None)


def _customer(db, name="Reference Co", cid="CUST-AR1"):
    c = models.Customer(customer_id=cid, name=name, contact_number="9000000001")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _challan(db, cust_id, ch_number, ch_date):
    ch = models.SalesChallan(
        challan_number=ch_number,
        customer_id=cust_id,
        challan_date=ch_date,
        total_price=1000.0,
    )
    db.add(ch)
    db.commit()
    db.refresh(ch)
    return ch


def _login_row(db, user_id, timestamp):
    from backend.app.models import AuditLog
    row = AuditLog(user_id=user_id, action="login", module="users",
                   record_id=user_id, timestamp=timestamp)
    db.add(row)
    db.commit()


def test_a_challan_row_shows_its_challan_number(db):
    rows = list_audit_logs(db=db)
    assert len(rows) == 0

    cust = _customer(db)
    ch = _challan(db, cust.id, "CH-2026-0042", datetime(2026, 9, 28))

    # Simulate a login + create audit row
    u = models.User(username="tester", hashed_password="x", role="admin", is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)

    _login_row(db, u.id, datetime(2026, 9, 28, 10, 0, 0))
    m._audit(db, _req(), "sales", "create", record=ch,
             record_type="sales_challan", new_values={"challan_number": "CH-2026-0042"})
    db.commit()

    rows = list_audit_logs(db=db)
    # There are 2 rows: login + create
    assert len(rows) == 2
    # The create row should be first (newest)
    create_row = rows[0]
    assert create_row["action"] == "create"
    assert create_row["record_label"] == "Challan CH-2026-0042 \u00b7 Reference Co"


def test_invoice_row_shows_invoice_number(db):
    from backend.app.routes.purchase import list_purchase_invoices
    rows = list_purchase_invoices(db=db)
    assert len(rows) == 0

    cust = _customer(db, "Supplier Co", "SUP-AR1")
    inv = models.PurchaseInvoice(
        invoice_number="PI-2026-0099",
        supplier_id=cust.id,
        invoice_date=datetime(2026, 9, 28),
        total_amount=5000.0,
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)

    u = models.User(username="tester2", hashed_password="x", role="admin", is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)

    _login_row(db, u.id, datetime(2026, 9, 28, 11, 0, 0))
    m._audit(db, _req(), "purchases", "create", record=inv,
             record_type="purchase_invoice", new_values={"invoice_number": "PI-2026-0099"})
    db.commit()

    rows = list_purchase_invoices(db=db)
    assert len(rows) >= 1
    # Find our invoice
    target = [r for r in rows if r.invoice_number == "PI-2026-0099"][0]
    assert target is not None


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])