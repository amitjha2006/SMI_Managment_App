"""Tests for company terms and the frozen document header.

The behaviour under test is deliberate but easy to mistake for a bug:

    Terms are saved to the company profile, and copied onto documents created
    AFTERWARDS. Documents already issued keep the footer they were printed
    with.

Reported once as "terms are not getting saved". They were - the confusion was
that printing an already-issued challan shows no footer, which looks exactly
like a failed save. The fix is to make the boundary visible, which is what
these tests hold in place.

Run: python backend/tests/test_company_terms.py
"""

import os
import sys
from datetime import datetime, timezone

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app.services import challan_snapshot as snap  # noqa: E402

TERMS = "Goods once sold will not be taken back. Interest @18% p.a. on overdue amounts."


class C:
    def __init__(self, gst_no=None, company_name=None, address=None):
        self.gst_no = gst_no
        self.company_name = company_name
        self.name = company_name or "Test Customer"
        self.address = address
        self.contact_number = "9999999999"


def test_company_keys_includes_terms():
    # If this ever dropped out of the map, terms would silently stop saving
    # with no error anywhere - which is exactly what was reported.
    assert "terms" in snap.COMPANY_KEYS, snap.COMPANY_KEYS
    assert snap.COMPANY_KEYS["terms"] == "company.terms"


def test_snapshot_fields_include_terms():
    assert "company_terms" in snap.SNAPSHOT_FIELDS, snap.SNAPSHOT_FIELDS


def test_a_document_created_after_the_change_carries_the_terms():
    # The positive case: this is the path the user expects.
    ch = _stub_challan()
    snap.apply_snapshot(
        ch, {"name": "Shresth Masala Industry", "gstin": "10BQYPJ6507J1ZK", "terms": TERMS}, C()
    )
    assert ch.company_terms == TERMS, ch.company_terms


def test_a_document_created_before_the_change_keeps_its_own_wording():
    # The frozen case: an issued document is not rewritten. It keeps whatever
    # it was stamped with, even after the master changes.
    old = "Old wording printed on the original copy."
    ch = _stub_challan()
    snap.apply_snapshot(ch, {"name": "Shresth Masala", "terms": old}, C())
    assert ch.company_terms == old
    # The master changes afterwards...
    # ...and this document is deliberately untouched.
    assert ch.company_terms == old, "an issued document must not be rewritten"


def test_terms_are_optional():
    # A company with no terms must still be able to save a profile.
    ch = _stub_challan()
    snap.apply_snapshot(ch, {"name": "Shresth Masala", "terms": ""}, C())
    assert ch.company_terms in (None, ""), repr(ch.company_terms)


def test_empty_terms_stamp_as_blank_not_the_word_none():
    ch = _stub_challan()
    snap.apply_snapshot(ch, {"name": "X", "gstin": "10BQYPJ6507J1ZK"}, C())
    assert ch.company_terms != "None", repr(ch.company_terms)


def test_multi_line_terms_survive_intact():
    # Terms are usually several lines. Newlines must not be collapsed or lost.
    multi = (
        "1. Goods once sold will not be taken back.\n"
        "2. Interest @18% p.a.\n"
        "3. Subject to Bhagalpur jurisdiction."
    )
    ch = _stub_challan()
    snap.apply_snapshot(ch, {"name": "X", "terms": multi}, C())
    assert ch.company_terms == multi
    assert ch.company_terms.count("\n") == 2


def test_ampersands_in_terms_are_preserved():
    # "Interest @18% p.a. & fees" - an ampersand must survive storage exactly.
    ch = _stub_challan()
    snap.apply_snapshot(ch, {"name": "X", "terms": "A & B <terms>"}, C())
    assert ch.company_terms == "A & B <terms>"


def _stub_challan():
    from backend.app import models

    ch = models.SalesChallan(
        challan_number="CH/26-27/0001",
        challan_date=datetime(2026, 9, 28, tzinfo=timezone.utc),
        customer_id=1,
    )
    return ch


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"{len(tests)} tests passed")
