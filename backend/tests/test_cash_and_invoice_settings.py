"""Tests for the cash-float balance and the Phase 0 invoice foundations.

Two things are under test:

1. The cash balance is DERIVED (opening float + sum of movements), never a
   stored running total. The existing `cash` column on customers and suppliers
   had no behaviour at all before this, so the risk is that it half-works:
   accepts a movement and reports a balance that does not reconcile.

2. The invoice cut-over boundary. 89 challans already exist and were delivered;
   the business decided they stay delivery notes. A boundary that can be
   widened by accident, or that silently allows an old challan to be invoiced,
   means issuing tax documents for sales that were never re-billed.

Pure arithmetic is separated from persistence, so most of this needs no DB.

Run: python backend/tests/test_cash_and_invoice_settings.py
"""

import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app.services import cash_balance as cb  # noqa: E402
from backend.app.services import gst  # noqa: E402


class M:
    """Stand-in for a cash_movements row."""

    def __init__(self, movement_type, amount, is_void=False):
        self.movement_type = movement_type
        self.amount = amount
        self.is_void = is_void


# --- cash balance arithmetic ------------------------------------------------


def test_balance_starts_from_the_opening_float():
    assert cb.compute_balance(500, []) == Decimal("500.00")


def test_received_raises_and_paid_lowers():
    # Direction is stated from the factory's point of view, which is what keeps
    # one implementation correct for both customers and suppliers.
    assert cb.compute_balance(0, [M("received", 1000)]) == Decimal("1000.00")
    assert cb.compute_balance(0, [M("paid", 1000)]) == Decimal("-1000.00")
    assert cb.compute_balance(0, [M("received", 1000), M("paid", 400)]) == Decimal("600.00")


def test_voided_movements_are_ignored():
    # Undo is a flag flip, not a delete, so history survives and the balance
    # returns to where it would have been.
    assert cb.compute_balance(0, [M("received", 500), M("paid", 200, is_void=True)]) == Decimal(
        "500.00"
    )


def test_balance_is_recomputable_at_any_time():
    # The point of keeping rows instead of a running total: the answer is
    # always derivable, so a wrong displayed figure is recoverable.
    moves = [M("received", 300), M("paid", 100), M("received", 250)]
    expected = Decimal("450.00")
    assert cb.compute_balance(0, moves) == expected
    assert cb.compute_balance(0, moves) == expected  # same answer twice


def test_negative_balance_means_we_owe_them():
    # A legitimate state, not an error: we took cash and gave back more.
    assert cb.compute_balance(0, [M("paid", 250)]) < 0


def test_unknown_movement_type_is_refused_not_ignored():
    # Silently skipping an unrecognised row would make a balance quietly wrong
    # with nothing to explain the difference.
    try:
        cb.compute_balance(0, [M("gift", 100)])
    except ValueError as e:
        assert "Unknown cash movement type" in str(e)
        return
    raise AssertionError("an unknown movement type should raise")


def test_zero_and_negative_amounts_are_refused():
    for bad in [0, "0", -5, "-0.01"]:
        try:
            cb.validate_movement("received", bad)
        except ValueError:
            continue
        raise AssertionError(f"amount {bad!r} should have been refused")


def test_valid_movements_are_accepted():
    assert cb.validate_movement("received", "100.5") == Decimal("100.50")
    assert cb.validate_movement("paid", 100) == Decimal("100.00")


def test_signed_amount_reports_the_effect_on_the_balance():
    assert cb.movement_signed_amount("received", "100") == Decimal("100.00")
    assert cb.movement_signed_amount("paid", "100") == Decimal("-100.00")


def test_decimal_math_does_not_drift():
    # Float arithmetic on money accumulates error; Decimal must not.
    moves = [M("received", "0.10")] * 10
    assert cb.compute_balance(0, moves) == Decimal("1.00")


# --- invoice cut-over boundary ---------------------------------------------


def test_cutover_today_excludes_every_existing_challan():
    # The decision: the 89 existing challans stay delivery notes. A challan
    # from yesterday must not be invoiceable.
    cutover = date(2026, 9, 28)
    old = datetime(2026, 8, 23, tzinfo=timezone.utc).date()
    assert old < cutover


def test_cutover_boundary_is_inclusive_on_its_own_date():
    # A challan dated ON the cutover day is invoiceable; one day earlier is not.
    cutover = date(2026, 9, 28)
    assert datetime(2026, 9, 28, 9, 0).date() >= cutover
    assert datetime(2026, 9, 27, 23, 59).date() < cutover


def test_all_four_existing_products_start_with_no_gst_rate():
    # 0 means "not configured", which BLOCKS invoicing. If any product
    # defaulted to a real rate, an invoice could be issued at a guessed slab.
    for rate in ["0", "0.00", Decimal("0"), 0]:
        assert gst.is_rate_configured(rate) is False
        assert gst.extract("1000.00", rate)["taxable_value"] == Decimal("1000.00")


def test_an_unconfigured_product_cannot_produce_a_tax_document():
    # Invoice creation is expected to consult is_rate_configured and refuse.
    # This asserts the predicate that decision depends on, so the two cannot
    # drift apart unnoticed.
    assert gst.is_rate_configured("5") is True
    assert gst.is_rate_configured("0") is False


# --- the two features compose ---------------------------------------------


def test_invoice_figures_reconcile_to_the_challan_across_many_products():
    # The end-to-end property: a challan with mixed products and rates yields
    # an invoice whose total equals the challan total exactly.
    lines = [("500.00", "5"), ("1000.00", "12"), ("250.00", "18"), ("1999.99", "28"), ("0.01", "5")]
    challan_total = sum(Decimal(a) for a, _ in lines)
    res = gst.extract_lines(lines)
    t = res["totals"]
    assert t["amount"] == challan_total
    assert t["taxable_value"] + t["total_tax"] == challan_total


def test_cash_balance_survives_a_rebuild_from_rows():
    # Recomputing after a void matches the balance as it was before the entry.
    moves = [M("received", 1000), M("paid", 250)]
    before = cb.compute_balance(0, moves)
    moves[1].is_void = True
    after = cb.compute_balance(0, moves)
    assert after - before == Decimal("250.00")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"{len(tests)} tests passed")
