"""Monthly Product Sales FY mode (April-March).

Calendar mode buckets January-December; FY mode buckets April-March of
the Indian financial year (fy=2026 is Apr 2026-Mar 2027). Same response
shape either way, so the pivot, drill-down and export work unchanged.

Run: python -m pytest backend/tests/test_monthly_fy.py -v
"""

import os
import sys
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _sale(db, number, when, amount):
    c = db.query(models.Customer).filter(
        models.Customer.customer_id == "C-FY").first()
    if not c:
        c = models.Customer(customer_id="C-FY", name="FY Buyer",
                            contact_number="9000000001")
        db.add(c)
        db.flush()
    p = db.query(models.Product).filter(
        models.Product.product_id == "P-FY1").first()
    if not p:
        p = models.Product(product_id="P-FY1", name="FY Masala")
        db.add(p)
        db.flush()
    ch = models.SalesChallan(
        challan_number=number, challan_date=when, customer_id=c.id,
        status="confirmed", total_price=Decimal(str(amount)),
        paid_amount=Decimal("0"), outstanding_amount=Decimal(str(amount)),
    )
    db.add(ch)
    db.flush()
    db.add(
        models.SalesChallanItem(
            challan_id=ch.id, product_id=p.id,
            packaging_size_id=1, quantity=Decimal("10"),
            weight=Decimal("5"), rate_per_kg=Decimal("200"),
            amount=Decimal(str(amount)),
        )
    )
    db.commit()


def test_fy_buckets_april_to_march(db):
    _sale(db, "CH-FY1", datetime(2026, 3, 15, 10, 0, 0), 1000)
    _sale(db, "CH-FY2", datetime(2026, 4, 10, 10, 0, 0), 2000)
    out = m.analysis_monthly_sales(fy=2026, db=db)
    keys = [mo["key"] for mo in out["months"]]
    assert keys[0] == "2026-04" and keys[-1] == "2027-03", keys
    assert [mo["label"] for mo in out["months"]][:2] == ["Apr", "May"]
    assert out["grand_total_amount"] == pytest.approx(2000.0), out
    assert out["fy"] == 2026 and out["year"] is None, out


def test_fy_boundary_splits_march_april(db):
    _sale(db, "CH-FY1", datetime(2026, 3, 15, 10, 0, 0), 1000)
    _sale(db, "CH-FY2", datetime(2026, 4, 10, 10, 0, 0), 2000)
    assert m.analysis_monthly_sales(fy=2025, db=db)["grand_total_amount"] == pytest.approx(1000.0)
    assert m.analysis_monthly_sales(year=2026, db=db)["grand_total_amount"] == pytest.approx(3000.0)


def test_bad_fy_refused(db):
    with pytest.raises(HTTPException) as e:
        m.analysis_monthly_sales(fy="nope", db=db)
    assert e.value.status_code == 400


def test_export_honours_fy(db):
    _sale(db, "CH-FY2", datetime(2026, 4, 10, 10, 0, 0), 2000)
    res = m.analysis_monthly_export(fy=2026, db=db)
    disp = res.headers["content-disposition"]
    assert "FY2026" in disp, disp


def test_fy_toggle_on_page():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    page = os.path.join(root, "frontend", "pages", "monthly-sales.html")
    with open(page, encoding="utf-8") as fh:
        html = fh.read()
    assert 'id="ms-fy"' in html, "no FY option on Monthly Product Sales"
    assert "toggleFyMode" in html and "fy=" in html, (
        "FY toggle never queries the FY endpoint"
    )


def test_calendar_year_selection_changes_data(db):
    """The endpoint used to clobber the `year` query param back to None, so
    every calendar-mode request answered with the current year and the Year
    dropdown appeared dead (only FY mode moved). A 2025 sale must come back
    under year=2025 and stay out of 2026."""
    _sale(db, "CH-Y1", datetime(2025, 7, 10, 10, 0, 0), 1500)
    got = m.analysis_monthly_sales(year=2025, db=db)
    assert got["grand_total_amount"] == pytest.approx(1500.0), got
    assert got["year"] == 2025 and got["fy"] is None, got
    other = m.analysis_monthly_sales(year=2026, db=db)
    assert other["grand_total_amount"] == pytest.approx(0.0), other
