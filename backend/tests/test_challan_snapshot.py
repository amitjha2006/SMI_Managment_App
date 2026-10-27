"""Tests for the frozen challan print header.

The rule under test: what a printed challan says must not change after the
challan has been created. A challan copies the company profile, the customer's
details and each line's product/size name onto itself, and editing any of those
masters later must not reach back and rewrite it.

The `company_snapshot_at` marker exists because a deliberately blank company
name is a legitimate value - testing "is the name set?" would confuse that with
"has this been stamped?", and the backfill would re-stamp forever.

Runs against an in-memory SQLite database, never the real one.
Pure function tests need no database; these do because stamping is a
persistence concern.

Run: python backend/tests/test_challan_snapshot.py
"""

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from datetime import datetime  # noqa: E402

from backend.app import models  # noqa: E402,F401
from backend.app.services import challan_snapshot as snap  # noqa: E402

PROFILE = {
    "name": "Shresth Masala Industries",
    "address": "Plot 14, MIDC, Andheri East, Mumbai 400093",
    "gstin": "27AABCS1234K1Z5",
    "phone": "+91 98765 43210",
    "email": "sales@shresth.example",
    "terms": "Goods once sold will not be taken back.",
}

_seq = iter(range(1, 10000))


def _customer(db, name="Patel Provisions", gst="27AAPCU1234M1Z5"):
    c = models.Customer(
        customer_id=f"CUST-{next(_seq):04d}",
        name=name,
        contact_number="9876500000",
        gst_no=gst,
        address="Market Yard, Surat",
        company_name=name,
    )
    db.add(c)
    db.commit()
    return c


def _challan(db, customer, number="CH-10001"):
    c = models.SalesChallan(
        challan_number=number, challan_date=datetime(2026, 9, 28), customer_id=customer.id
    )
    db.add(c)
    db.commit()
    return c


# --- pure helpers -----------------------------------------------------------


def test_customer_snapshot_copies_only_print_fields(db):
    c = _customer(db)
    got = snap.customer_snapshot(c)
    assert got["company_name"] == "Patel Provisions"
    assert got["gst_no"] == "27AAPCU1234M1Z5"
    assert got["address"] == "Market Yard, Surat"
    assert got["contact_number"] == "9876500000"


def test_customer_snapshot_survives_a_missing_customer():
    got = snap.customer_snapshot(None)
    assert set(got.values()) == {None}


def test_blank_customer_fields_become_none_not_empty_strings(db):
    # "" would print as a stray blank line; None prints nothing.
    c = models.Customer(
        customer_id="CUST-9001",
        name="No GST Co",
        contact_number="9000000000",
        gst_no="   ",
        address="",
        company_name="",
    )
    db.add(c)
    db.commit()
    got = snap.customer_snapshot(c)
    assert got["gst_no"] is None
    assert got["address"] is None
    assert got["company_name"] is None


# --- stamping --------------------------------------------------------------


def test_apply_snapshot_freezes_every_field(db):
    c = _customer(db)
    ch = _challan(db, c)
    assert snap.is_stamped(ch) is False
    snap.apply_snapshot(ch, PROFILE, c)
    assert snap.is_stamped(ch) is True
    assert ch.company_name == PROFILE["name"]
    assert ch.company_gstin == PROFILE["gstin"]
    assert ch.company_terms == PROFILE["terms"]
    assert ch.cust_company_name == "Patel Provisions"
    assert ch.cust_gst_no == "27AAPCU1234M1Z5"
    assert ch.company_snapshot_at is not None


def test_a_blank_company_name_is_still_stamped(db):
    # The whole reason for the separate marker: an intentionally blank name
    # must not look like "never stamped".
    c = _customer(db)
    ch = _challan(db, c)
    snap.apply_snapshot(ch, {"name": "", "gstin": ""}, c)
    assert ch.company_name is None
    assert snap.is_stamped(ch) is True


def test_stamp_unstamped_only_touches_null_rows(db):
    c = _customer(db)
    old = _challan(db, c, "CH-10001")
    snap.apply_snapshot(old, PROFILE, c)
    fresh = _challan(db, c, "CH-10002")  # left un-stamped
    db.commit()
    # The precondition: exactly one challan is un-stamped, so a count of 1
    # proves the backfill skipped `old` rather than re-stamping everything.
    assert snap.is_stamped(old) is True
    assert snap.is_stamped(fresh) is False

    stamped = snap.stamp_unstamped(db, PROFILE, customer_lookup=lambda _i: c)
    db.commit()
    assert stamped == 1, stamped
    db.refresh(old)
    assert old.company_name == PROFILE["name"]
    db.refresh(fresh)
    assert fresh.company_name == PROFILE["name"]


def test_a_later_profile_save_cannot_rewrite_frozen_challans(db):
    # The behaviour the whole feature exists for.
    c = _customer(db)
    ch = _challan(db, c)
    snap.apply_snapshot(ch, PROFILE, c)
    db.commit()

    later = dict(PROFILE, name="RENAMED LTD", address="Elsewhere", gstin="29AAAAA0000A1Z5")
    again = snap.stamp_unstamped(db, later, customer_lookup=lambda _i: c)
    db.refresh(ch)
    assert again == 0, "nothing new should have been stamped"
    assert ch.company_name == PROFILE["name"]
    assert ch.company_gstin == PROFILE["gstin"]
    assert ch.company_address == PROFILE["address"]


def test_stamp_unstamped_is_idempotent(db):
    c = _customer(db)
    _challan(db, c, "CH-10001")
    _challan(db, c, "CH-10002")
    db.commit()
    first = snap.stamp_unstamped(db, PROFILE, customer_lookup=lambda _i: c)
    db.commit()
    second = snap.stamp_unstamped(db, PROFILE, customer_lookup=lambda _i: c)
    assert first == 2
    assert second == 0


def test_each_challan_keeps_its_own_customer(db):
    # Two customers, one profile: the backfill must not copy customer A's
    # address onto customer B's challan.
    a = _customer(db, "Alpha Traders", "27AAAAA0000A1Z5")
    b = _customer(db, "Beta Stores", "29BBBBB1111B1Z5")
    ca = _challan(db, a, "CH-10001")
    cb = _challan(db, b, "CH-10002")
    db.commit()
    snap.stamp_unstamped(db, PROFILE, customer_lookup=lambda i: db.get(models.Customer, i))
    db.commit()  # the helper does not commit; the caller does
    db.refresh(ca)
    db.refresh(cb)
    assert ca.cust_company_name == "Alpha Traders"
    assert cb.cust_company_name == "Beta Stores"
    assert ca.cust_gst_no == "27AAAAA0000A1Z5"
    assert cb.cust_gst_no == "29BBBBB1111B1Z5"


def test_stamp_unstamped_with_nothing_to_do(db):
    c = _customer(db)
    _challan(db, c)
    snap.stamp_unstamped(db, PROFILE, customer_lookup=lambda _i: c)
    db.commit()
    assert snap.stamp_unstamped(db, PROFILE, customer_lookup=lambda _i: c) == 0


# --- item names ------------------------------------------------------------


def _product_with_size(db, name="Coriander", size="100g"):
    p = models.Product(product_id="P-1", name=name)
    db.add(p)
    db.commit()
    s = models.PackagingSize(product_id=p.id, size_name=size, weight_in_kg=Decimal("0.1"))
    db.add(s)
    db.commit()
    return p, s


def test_fill_missing_item_names_freezes_the_descriptions(db):
    c = _customer(db)
    ch = _challan(db, c)
    p, s = _product_with_size(db)
    db.add(
        models.SalesChallanItem(
            challan_id=ch.id,
            product_id=p.id,
            packaging_size_id=s.id,
            quantity=Decimal("10"),
            weight=Decimal("1.0"),
            rate_per_kg=Decimal("300"),
            amount=Decimal("300"),
        )
    )
    db.commit()

    assert snap.fill_missing_item_names(db) == 1
    row = db.query(models.SalesChallanItem).one()
    assert row.product_name == "Coriander"
    assert row.size_name == "100g"


def test_fill_missing_item_names_never_overwrites_a_real_value(db):
    c = _customer(db)
    ch = _challan(db, c)
    p, s = _product_with_size(db, "Coriander", "100g")
    db.add(
        models.SalesChallanItem(
            challan_id=ch.id,
            product_id=p.id,
            packaging_size_id=s.id,
            quantity=Decimal("10"),
            weight=Decimal("1.0"),
            rate_per_kg=Decimal("300"),
            amount=Decimal("300"),
            product_name="OLD NAME",
            size_name="OLD SIZE",
        )
    )
    db.commit()

    assert snap.fill_missing_item_names(db) == 0
    row = db.query(models.SalesChallanItem).one()
    assert row.product_name == "OLD NAME"
    assert row.size_name == "OLD SIZE"


def test_fill_missing_item_names_is_idempotent(db):
    c = _customer(db)
    ch = _challan(db, c)
    p, s = _product_with_size(db)
    db.add(
        models.SalesChallanItem(
            challan_id=ch.id,
            product_id=p.id,
            packaging_size_id=s.id,
            quantity=Decimal("10"),
            weight=Decimal("1.0"),
            rate_per_kg=Decimal("300"),
            amount=Decimal("300"),
        )
    )
    db.commit()
    assert snap.fill_missing_item_names(db) == 1
    db.commit()
    assert snap.fill_missing_item_names(db) == 0


# --- report shape ----------------------------------------------------------


def test_describe_reports_the_stamp_state(db):
    c = _customer(db)
    ch = _challan(db, c)
    d = snap.describe(ch)
    assert d["stamped"] is False
    assert "company_gstin" in d
    snap.apply_snapshot(ch, PROFILE, c)
    d = snap.describe(ch)
    assert d["stamped"] is True
    assert d["company_name"] == PROFILE["name"]


def test_company_profile_reads_settings_rows(db):
    from backend.app.models import ApplicationSetting

    # Keyed by field name (name/address/...), not by the "company." storage key.
    assert snap.company_profile(db) == dict.fromkeys(snap.COMPANY_KEYS, "")
    db.add(
        ApplicationSetting(
            key="company.name",
            value="  Shresth Masala Industries  ",
            category="company",
            is_editable=True,
        )
    )
    db.add(
        ApplicationSetting(
            key="company.gstin", value="27AABCS1234K1Z5", category="company", is_editable=True
        )
    )
    db.commit()
    prof = snap.company_profile(db)
    assert prof["name"] == "Shresth Masala Industries"  # trimmed
    assert prof["gstin"] == "27AABCS1234K1Z5"
    assert prof["address"] == ""  # absent key is empty


if __name__ == "__main__":
    # Delegate to pytest. Hand-calling the test functions cannot supply the
    # fixtures from conftest, so a direct run of a database test would fail
    # while the suite passed - two different answers to the same question.
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
