"""Tests for the Indian amount-in-words helper used on printed challans.

The Indian grouping is the point: 1,23,456 reads as "one lakh twenty three
thousand four hundred and fifty six", not "one hundred and twenty three
thousand". A challan total written in the wrong grouping is a reconciliation
error waiting to happen.

Pure function, no framework imports.

Run: python backend/tests/test_amount_in_words.py
"""

import os
import sys
from decimal import Decimal

# Runnable directly as well as under pytest.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app.services.amount_in_words import (  # noqa: E402
    _int_to_words,
    _round_paise,
    amount_in_words,
)


def test_single_digits():
    assert amount_in_words(1) == "Rupees One Only"
    assert amount_in_words(9) == "Rupees Nine Only"


def test_zero():
    assert amount_in_words(0) == "Rupees Zero Only"
    assert amount_in_words("0.00") == "Rupees Zero Only"


def test_teens_and_tens():
    assert amount_in_words(11) == "Rupees Eleven Only"
    assert amount_in_words(19) == "Rupees Nineteen Only"
    assert amount_in_words(20) == "Rupees Twenty Only"
    assert amount_in_words(99) == "Rupees Ninety Nine Only"


def test_hundreds():
    assert amount_in_words(100) == "Rupees One Hundred Only"
    assert amount_in_words(101) == "Rupees One Hundred and One Only"
    assert amount_in_words(999) == "Rupees Nine Hundred and Ninety Nine Only"


def test_thousand_lakh_crore_grouping():
    # These are the boundaries that distinguish Indian from English numbering.
    assert amount_in_words(1000) == "Rupees One Thousand Only"
    assert amount_in_words(100000) == "Rupees One Lakh Only"
    assert amount_in_words(1000000) == "Rupees Ten Lakh Only"
    assert amount_in_words(10000000) == "Rupees One Crore Only"
    assert amount_in_words(250000) == "Rupees Two Lakh Fifty Thousand Only"


def test_mixed_grouping():
    assert (
        amount_in_words(123456)
        == "Rupees One Lakh Twenty Three Thousand Four Hundred and Fifty Six Only"
    )
    assert (
        amount_in_words(602969) == "Rupees Six Lakh Two Thousand Nine Hundred and Sixty Nine Only"
    )


def test_paise():
    assert amount_in_words("0.50") == "Rupees Zero and Fifty Paise Only"
    assert amount_in_words("123456.75") == (
        "Rupees One Lakh Twenty Three Thousand Four Hundred and Fifty Six "
        "and Seventy Five Paise Only"
    )
    # Exactly whole rupees must not mention paise at all.
    assert amount_in_words("100.00") == "Rupees One Hundred Only"


def test_paise_do_not_borrow_from_rupees():
    # Regression: Decimal.to_integral_value() rounds half-even by default, so
    # 999.99 was splitting into 1000 rupees and -1 paise.
    assert _round_paise(Decimal("999.99")) == (999, 99)
    assert (
        amount_in_words("999.99")
        == "Rupees Nine Hundred and Ninety Nine and Ninety Nine Paise Only"
    )
    assert _round_paise(Decimal("123456.75")) == (123456, 75)


def test_rounding_carries_into_rupees():
    # 0.999 rounds up to a whole rupee, never to "negative one paise".
    rupees, paise = _round_paise(Decimal("0.999"))
    assert rupees == 1
    assert paise == 0
    assert amount_in_words("0.999") == "Rupees One Only"


def test_never_raises_on_odd_input():
    # Called while rendering a document, so it must not break the printout.
    for bad in (None, "", "abc", float("nan"), Decimal("-50")):
        out = amount_in_words(bad)
        assert out.startswith("Rupees ") and out.endswith("Only"), out


def test_always_ends_with_only():
    for v in ("0", "1", "99.99", "10000000.01", "123456.78"):
        assert amount_in_words(Decimal(v)).endswith(" Only"), v


def test_internal_conversion_is_consistent():
    # Every integer up to 2000 must produce a non-empty, space-normalised string.
    for n in range(0, 2000):
        words = _int_to_words(n)
        assert words and "  " not in words, (n, words)
        assert not words.endswith(" "), (n, words)


if __name__ == "__main__":
    # Delegate to pytest. Hand-calling the test functions cannot supply the
    # fixtures from conftest, so a direct run of a database test would fail
    # while the suite passed - two different answers to the same question.
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
