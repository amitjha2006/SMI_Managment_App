"""Tests for GST extraction from GST-INCLUSIVE amounts.

This is the arithmetic a tax invoice is built from, so the tests are written
around consequences rather than implementation: an invoice must foot to its
challan, to the paisa, for every rate and every combination of rates.

The identity under test throughout:

    taxable_value + total_tax == amount      (per line and in total)

That is what makes a tax invoice defensible. If it breaks, the invoice does
not match the challan it was pulled from.

Pure functions, no database.

Run: python backend/tests/test_gst.py
"""

import os
import sys
from decimal import Decimal

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app.services import gst  # noqa: E402

D = Decimal


def test_15_percent_splits_into_1000_plus_150():
    # The worked example: 1150 quoted inclusive of 15% is 1000 of goods.
    r = gst.extract("1150.00", "15")
    assert r["taxable_value"] == D("1000.00"), r
    assert r["total_tax"] == D("150.00"), r
    assert r["amount"] == D("1150.00"), r


def test_5_percent_and_12_percent_common_slabs():
    assert gst.extract("1050.00", "5")["taxable_value"] == D("1000.00")
    assert gst.extract("1120.00", "12")["taxable_value"] == D("1000.00")
    assert gst.extract("1180.00", "18")["taxable_value"] == D("1000.00")


def test_taxable_plus_tax_always_equals_amount():
    # The core invariant, swept across rates and awkward amounts.
    amounts = [
        "1.00",
        "9.99",
        "100.00",
        "333.33",
        "1150.00",
        "12076.50",
        "14025.00",
        "0.05",
        "99999.99",
        "1151.11",
        "7.77",
    ]
    for rate in ["5", "12", "18", "28", "1", "2.5", "7", "13.75"]:
        for amt in amounts:
            r = gst.extract(amt, rate)
            assert r["taxable_value"] + r["total_tax"] == r["amount"], (
                f"rate={rate} amount={amt} -> {r}"
            )


def test_mixed_rates_on_one_invoice_reconcile():
    # Why the rate is per product: one invoice, three different slabs.
    res = gst.extract_lines(
        [
            ("1150.00", "15"),  # 1000.00 + 150.00
            ("1050.00", "5"),  # 1000.00 +  50.00
            ("1120.00", "12"),  # 1000.00 + 120.00
        ]
    )
    t = res["totals"]
    assert t["taxable_value"] == D("3000.00"), t
    assert t["total_tax"] == D("320.00"), t
    assert t["amount"] == D("3320.00"), t
    assert t["taxable_value"] + t["total_tax"] == t["amount"]


def test_totals_are_summed_per_line_not_rederived():
    # A subtle trap this guards: once rates differ you cannot re-derive the
    # taxable total from the grand amount, because no single rate applies.
    # Both lines below are exactly 1000.00 of taxable value, so the sum is
    # unambiguous - whereas re-deriving 2300.00 at a blended 10% would give
    # 2090.91 and silently disagree by 9.33.
    res = gst.extract_lines([("1150.00", "15"), ("1060.00", "6")])
    t = res["totals"]
    assert t["taxable_value"] == D("2000.00"), t
    assert t["amount"] == D("2210.00"), t
    assert t["total_tax"] == D("210.00"), t
    # and demonstrate the wrong answer is genuinely different
    assert gst.extract("2210.00", "10")["taxable_value"] != t["taxable_value"]


def test_same_amount_at_different_rates_splits_differently():
    # 1150 is 1000+150 at 15%, but 1095.24+54.76 at 5%. Same quoted figure,
    # different tax content - which is exactly why the rate must be per line.
    at15 = gst.extract("1150.00", "15")
    at5 = gst.extract("1150.00", "5")
    assert at15["taxable_value"] == D("1000.00")
    assert at5["taxable_value"] == D("1095.24")
    assert at15["total_tax"] != at5["total_tax"]


def test_cgst_and_sgst_are_halves_that_re_add_exactly():
    # Intra-state supply splits into two halves. An odd paisa must not
    # vanish or the halves stop matching the tax total.
    for amt in ["1150.01", "1050.03", "999.99", "1.01", "7.77", "1120.07"]:
        for rate in ["5", "12", "18", "28"]:
            r = gst.extract(amt, rate)
            assert r["cgst"] + r["sgst"] == r["total_tax"], (amt, rate, r)


def test_rounding_residue_lands_on_sgst():
    # 1050.01 at 18% has a tax of 160.17, which cannot split into two equal
    # halves. CGST takes the rounded half (80.09) and SGST absorbs the
    # residue (80.08), so the halves still re-add to the total.
    r = gst.extract("1050.01", "18")
    assert r["total_tax"] == D("160.17"), r
    assert r["cgst"] == D("80.09"), r
    assert r["sgst"] == D("80.08"), r
    assert r["cgst"] + r["sgst"] == r["total_tax"]


def test_rounding_lands_on_the_taxable_value_not_the_tax():
    # 1050.01 at 5%: the exact taxable is 1000.0095..., which rounds UP to
    # 1000.01, so the tax comes out a clean 50.00. The paisa is absorbed by
    # whichever component absorbs it - what must never happen is the two
    # stopping adding up to the amount.
    r = gst.extract("1050.01", "5")
    assert r["taxable_value"] == D("1000.01"), r
    assert r["total_tax"] == D("50.00"), r
    assert r["taxable_value"] + r["total_tax"] == r["amount"]


def test_zero_amount_is_handled():
    r = gst.extract("0", "18")
    assert r["taxable_value"] == D("0.00")
    assert r["total_tax"] == D("0.00")
    assert r["cgst"] == D("0.00") and r["sgst"] == D("0.00")


def test_rate_of_zero_is_treated_as_unset_not_free():
    # 0 means "nobody has set this product's rate yet". It must not produce a
    # convincing-looking nil-rated invoice, so is_rate_configured says no.
    assert gst.is_rate_configured(0) is False
    assert gst.is_rate_configured("0.00") is False
    assert gst.is_rate_configured(D("0")) is False
    assert gst.is_rate_configured(5) is True
    assert gst.is_rate_configured("0.01") is True


def test_a_genuine_nil_rated_line_still_computes():
    # Guard: if nil-rated goods ever do need invoicing, the math is still
    # right - taxable equals the amount and the tax is zero.
    r = gst.extract("500.00", "0")
    assert r["taxable_value"] == D("500.00")
    assert r["total_tax"] == D("0.00")
    assert r["taxable_value"] + r["total_tax"] == r["amount"]


def test_bad_rates_are_refused():
    for bad in ["-1", "100", "150", "-0.5"]:
        try:
            gst.normalise_rate(bad)
        except gst.GstError:
            continue
        raise AssertionError(f"rate {bad!r} should have been refused")


def test_negative_amount_is_refused():
    try:
        gst.extract("-100", "18")
    except gst.GstError:
        return
    raise AssertionError("a negative amount should be refused")


def test_rates_accept_string_int_and_decimal_forms():
    for form in [5, "5", 5.0, "5.0", D("5"), D("5.00"), "5.00"]:
        assert gst.normalise_rate(form) == D("5"), form


def test_fractional_rate_is_not_silently_halved():
    # 0.18 means someone entered a fraction. Treat it as 0.18% would be wrong
    # and undetectable later, so it is caught by the range check only if it
    # is large - confirm a plausible-but-wrong input is at least visible.
    assert gst.normalise_rate("0.18") == D("0.18")


def test_empty_line_list_totals_to_zero():
    res = gst.extract_lines([])
    t = res["totals"]
    assert t["amount"] == D("0.00")
    assert t["taxable_value"] + t["total_tax"] == t["amount"]


def test_real_challan_totals_from_the_live_data_extract_cleanly():
    # The four largest real challan totals, as sanity that production data
    # flows through this without an exception.
    for amt in ["14025.00", "12076.50", "3250.00", "1800.00"]:
        for rate in ["5", "12", "18"]:
            r = gst.extract(amt, rate)
            assert r["taxable_value"] + r["total_tax"] == r["amount"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"{len(tests)} tests passed")
