"""The finished-goods packet balance that backs the dashboard card.

A packet count is only meaningful beside the pack size it came from, so the
endpoint reports one row per product AND size rather than one per product.
"""

import sys

import pytest

ROOT = r"C:\Shresth_Masala_Application"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402


@pytest.fixture()
def session():
    from backend.app import models  # noqa: F401
    from backend.app.core.database import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng)
    db = Session()
    db.execute(text("PRAGMA foreign_keys=ON"))
    yield db
    db.close()
    eng.dispose()


def _add_product(db, name, category="Dry Masala"):
    """Product.category is a read-only property over category_id, so the
    category has to be created as a row and referenced, not assigned."""
    from backend.app.models import Product, ProductCategory

    cat = db.query(ProductCategory).filter(ProductCategory.name == category).first()
    if cat is None:
        cat = ProductCategory(name=category)
        db.add(cat)
        db.flush()
    # product_id is the NOT NULL business code, distinct from the primary key.
    code = f"P-TEST-{db.query(Product).count() + 1}"
    p = Product(
        product_id=code,
        name=name,
        category_id=cat.id,
        is_active=True,
        gst_rate=5,
        hsn_code="09109999",
    )
    db.add(p)
    db.flush()
    return p


def _add_size(db, product_id, name, weight_kg):
    from backend.app.models import PackagingSize

    s = PackagingSize(
        product_id=product_id, size_name=name, weight_in_kg=weight_kg, packets_per_unit=1
    )
    db.add(s)
    db.flush()
    return s


def _move(db, product_id, size_id, mtype, kg):
    from backend.app.models import FinishedProductStockMovement

    db.add(
        FinishedProductStockMovement(
            product_id=product_id, packaging_size_id=size_id, movement_type=mtype, quantity=kg
        )
    )


def _balance(db):
    """Call the route function directly, bypassing HTTP.

    The rest of the suite avoids TestClient (no httpx2), so the handler is
    invoked with an explicit session instead.
    """
    from backend import main

    return main.finished_stock_balance(db)


def test_packets_use_each_sizes_own_weight(session):
    """The whole point of the per-size split: 50g and 1kg are not comparable."""
    p = _add_product(session, "Chilli")
    s50 = _add_size(session, p.id, "50g", 0.05)
    s1k = _add_size(session, p.id, "1kg", 1.0)
    _move(session, p.id, s50.id, "PRODUCTION", 5.0)  # 100 packets
    _move(session, p.id, s1k.id, "PRODUCTION", 4.0)  # 4 packets

    rows = {r["size_name"]: r for r in _balance(session)["rows"]}
    assert rows["50g"]["stock_packets"] == pytest.approx(100.0)
    assert rows["1kg"]["stock_packets"] == pytest.approx(4.0)
    assert rows["50g"]["stock_kg"] == pytest.approx(5.0)
    assert rows["1kg"]["stock_kg"] == pytest.approx(4.0)


def test_sales_and_wastage_reduce_the_balance(session):
    p = _add_product(session, "Turmeric")
    s = _add_size(session, p.id, "100g", 0.1)
    _move(session, p.id, s.id, "PRODUCTION", 20.0)  # 200 packets
    _move(session, p.id, s.id, "OPENING", 5.0)  #  50 packets
    _move(session, p.id, s.id, "SALES", 12.0)  # -120 packets
    _move(session, p.id, s.id, "WASTAGE", 3.0)  #  -30 packets

    row = _balance(session)["rows"][0]
    assert row["stock_kg"] == pytest.approx(25.0)
    assert row["sold_kg"] == pytest.approx(12.0)
    assert row["balance_kg"] == pytest.approx(10.0)
    assert row["balance_packets"] == pytest.approx(100.0)


def test_negative_balance_is_reported_not_hidden(session):
    """Overselling a size is a real state; the view must not conceal it."""
    p = _add_product(session, "Coriander")
    s = _add_size(session, p.id, "1kg", 1.0)
    _move(session, p.id, s.id, "PRODUCTION", 4.0)
    _move(session, p.id, s.id, "SALES", 13.0)

    out = _balance(session)
    row = out["rows"][0]
    assert row["balance_kg"] == pytest.approx(-9.0)
    assert row["balance_packets"] == pytest.approx(-9.0)
    assert row["is_negative"] is True
    assert out["meta"]["negative_rows"] == 1


def test_every_size_is_listed_even_with_no_history(session):
    p = _add_product(session, "SMI Chilli")
    _add_size(session, p.id, "50g", 0.05)
    _add_size(session, p.id, "250g", 0.25)

    out = _balance(session)
    names = sorted(r["size_name"] for r in out["rows"])
    assert names == ["250g", "50g"], "a size with no movements must still appear"
    assert all(r["stock_kg"] == 0 for r in out["rows"])


def test_inactive_products_are_excluded(session):
    p = _add_product(session, "Retired")
    s = _add_size(session, p.id, "100g", 0.1)
    _move(session, p.id, s.id, "PRODUCTION", 5.0)
    p.is_active = False
    session.flush()

    assert _balance(session)["rows"] == []


def test_inactive_sizes_are_still_reported(session):
    """A deactivated pack size still holds stock, so it must stay visible."""
    p = _add_product(session, "Chilli")
    s = _add_size(session, p.id, "100g", 0.1)
    _move(session, p.id, s.id, "PRODUCTION", 5.0)
    s.is_active = False
    session.flush()

    rows = _balance(session)["rows"]
    assert [r["size_name"] for r in rows] == ["100g"]
    assert rows[0]["balance_kg"] == pytest.approx(5.0)


def test_size_without_a_weight_reports_zero_packets(session):
    """Packets cannot be derived from a size with no declared weight."""
    p = _add_product(session, "Odd")
    s = _add_size(session, p.id, "unknown", 0)
    _move(session, p.id, s.id, "PRODUCTION", 5.0)

    row = _balance(session)["rows"][0]
    assert row["stock_kg"] == pytest.approx(5.0), "kg is still known"
    assert row["stock_packets"] == 0.0
    assert row["balance_packets"] == 0.0


def test_totals_are_the_sum_of_the_rows(session):
    p = _add_product(session, "Mix")
    a = _add_size(session, p.id, "100g", 0.1)
    b = _add_size(session, p.id, "500g", 0.5)
    _move(session, p.id, a.id, "PRODUCTION", 10.0)
    _move(session, p.id, b.id, "PRODUCTION", 25.0)
    _move(session, p.id, b.id, "SALES", 5.0)

    out = _balance(session)
    t = out["totals"]
    assert t["stock_kg"] == pytest.approx(35.0)
    assert t["balance_kg"] == pytest.approx(30.0)
    # 10kg of 100g is 100 packets; 25kg of 500g is 50. Stock 150.
    # 5kg of 500g sold is 10 packets, leaving 40 of that size: balance 140.
    assert t["stock_packets"] == pytest.approx(150.0)
    assert t["balance_packets"] == pytest.approx(140.0)


def test_agrees_with_the_existing_per_product_endpoint(session):
    """/finished-stock aggregates per product; the new rows must reconcile to it.

    This is the guard against the two views drifting apart, which is what made
    the earlier per-packet question hard to answer confidently.
    """
    from backend import main

    p = _add_product(session, "Coriander")
    s = _add_size(session, p.id, "100g", 0.1)
    _move(session, p.id, s.id, "PRODUCTION", 45.0)
    _move(session, p.id, s.id, "OPENING", 5.0)
    _move(session, p.id, s.id, "SALES", 14.64)

    per_size = _balance(session)["totals"]["balance_kg"]
    per_product = sum(r["total_kg"] for r in main.finished_stock(session))
    assert per_size == pytest.approx(per_product)
