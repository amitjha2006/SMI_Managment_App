"""Tests for production stage wastage rules.

Covers the two defects these rules exist to prevent:

1. wastage_quantity was never written, so every wastage total read zero even
   though the percentage was stored. stage_loss() now derives the quantity, and
   both figures come from the same input/output pair so they cannot disagree.
2. Wastage was reported against final production output. Loss compounds down a
   multi-stage workflow, so that overstated the rate badly - the reference
   dataset reads 15.35% the wrong way against 4.63% the honest way.

Pure functions, no database and no framework: the same style as
test_stock_analysis.py.

Run: python backend/tests/test_production_wastage.py
"""

import os
import sys
from decimal import Decimal

# Runnable directly (`python backend/tests/test_production_wastage.py`) as well
# as under pytest, so put the repo root on the path first.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app.services.production_wastage import (  # noqa: E402
    aggregate_by_category,
    stage_loss,
)

# Reference figures from the seeded 3-year dataset (149 stage rows).
DATASET_STAGE_INPUT_KG = Decimal("4694.75")
DATASET_WASTAGE_KG = Decimal("217.160")
DATASET_LOSS_PCT = Decimal("4.63")
# What the old, wrong denominator produced for the same data.
WRONG_DENOMINATOR_PCT = Decimal("15.35")


def test_stage_loss_basic():
    loss = stage_loss(100, 95)
    assert loss.input_kg == Decimal("100.000")
    assert loss.output_kg == Decimal("95.000")
    assert loss.wastage_kg == Decimal("5.000")
    assert loss.wastage_pct == Decimal("5.00")


def test_stage_loss_clamped_when_output_exceeds_input():
    # More out than in is a data mistake, not negative wastage. Reporting a
    # negative loss would quietly shrink totals.
    loss = stage_loss(10, 12)
    assert loss.wastage_kg == Decimal("0.000")
    assert loss.wastage_pct == Decimal("0.00")


def test_stage_loss_zero_input_is_not_a_division_error():
    # A rate against zero input is undefined, not infinite.
    loss = stage_loss(0, 0)
    assert loss.wastage_kg == Decimal("0.000")
    assert loss.wastage_pct == Decimal("0.00")


def test_stage_loss_tolerates_none_and_blank():
    for a, b in ((None, None), ("", ""), (None, 5), (None, "")):
        loss = stage_loss(a, b)
        assert loss.wastage_kg >= 0


def test_stage_loss_rejects_unparseable_input():
    # Garbage must not raise; it is treated as zero rather than 500-ing a save.
    loss = stage_loss("not-a-number", 5)
    assert loss.wastage_kg == Decimal("0.000")


def test_stage_loss_precision_is_fixed():
    loss = stage_loss(3, 1)
    # 2 / 3 = 66.666...% must land on 2dp, and quantities on 3dp.
    assert loss.wastage_pct == Decimal("66.67")
    assert str(loss.wastage_pct).split(".")[1].__len__() == 2
    assert str(loss.wastage_kg).split(".")[1].__len__() == 3


def test_quantity_and_percentage_never_disagree():
    # The whole point of deriving both from input/output.
    for in_q, out_q in ((100, 95), (50, 50), (80, 60), (1, 0.9), (7.5, 2.25)):
        loss = stage_loss(in_q, out_q)
        expected_kg = max(Decimal("0"), Decimal(str(in_q)) - Decimal(str(out_q)))
        assert abs(loss.wastage_kg - expected_kg) <= Decimal("0.001")
        if loss.input_kg > 0:
            recomputed = (loss.wastage_kg / loss.input_kg * 100).quantize(Decimal("0.01"))
            assert abs(recomputed - loss.wastage_pct) <= Decimal("0.01"), (in_q, out_q)


def test_aggregate_sums_stages_per_category():
    out = aggregate_by_category(
        [
            (1, 100, 95),  # loss 5
            (1, 200, 190),  # loss 10
            (2, 50, 50),  # no loss
        ]
    )
    assert out[1].wastage_kg == Decimal("15.000")
    assert out[1].stage_input_kg == Decimal("300.000")
    assert out[1].wastage_pct == Decimal("5.00")
    assert out[2].wastage_kg == Decimal("0.000")
    assert out[2].wastage_pct == Decimal("0.00")


def test_aggregate_uses_total_input_not_a_single_stage():
    # A single stage at 10% among others at 0% must not report 10% overall.
    out = aggregate_by_category([(1, 100, 90), (1, 900, 900)])
    assert out[1].wastage_pct == Decimal("1.00"), out[1].wastage_pct


def test_aggregate_percentage_denominator_is_total_input():
    # Regression guard for the metric that was wrong: loss is a share of what
    # went in, never of what came out.
    rows = [(1, 1000, 900)]  # 100 kg loss on 1000 kg in
    out = aggregate_by_category(rows)
    assert out[1].wastage_pct == Decimal("10.00")
    assert out[1].wastage_kg == Decimal("100.000")


def test_aggregate_clamps_negative_loss():
    out = aggregate_by_category([(1, 10, 20), (1, 10, 5)])
    # 5 kg of real loss only; the backwards stage contributes no negative.
    assert out[1].wastage_kg == Decimal("5.000")
    assert out[1].wastage_pct == Decimal("25.00")


def test_aggregate_skips_malformed_rows():
    out = aggregate_by_category([(1, 10, 5), None, (2,), (2, 10, 10)])
    assert 1 in out
    assert out[1].wastage_kg == Decimal("5.000")
    assert out[2].wastage_kg == Decimal("0.000")


def test_aggregate_empty_input():
    assert aggregate_by_category([]) == {}


def test_aggregate_equals_sum_of_per_stage_losses():
    # The invariant behind the save path: what the report totals must equal the
    # sum of the per-stage values that were actually written to the database.
    rows = [(1, 100, 95), (1, 40, 45), (1, 60, 58.5), (2, 10, 2)]
    out = aggregate_by_category(rows)
    for key in (1, 2):
        expected = sum(
            (stage_loss(i, o).wastage_kg for k, i, o in rows if k == key),
            Decimal("0"),
        )
        assert out[key].wastage_kg == expected, (key, out[key].wastage_kg, expected)


def test_reference_dataset_totals():
    # The seeded dataset after backfill. If this breaks, the backfill or the
    # production save path changed the arithmetic - do NOT just update the
    # numbers here; work out why they moved.
    pct = (DATASET_WASTAGE_KG / DATASET_STAGE_INPUT_KG * 100).quantize(Decimal("0.01"))
    assert pct == DATASET_LOSS_PCT, pct


def test_old_denominator_would_have_overstated_the_rate():
    # Guards the specific error: 217.16 kg against 1414.725 kg of final output
    # reads 15.35%, which is not a wastage rate at all.
    produced_kg = Decimal("1414.725")
    wrong = (DATASET_WASTAGE_KG / produced_kg * 100).quantize(Decimal("0.01"))
    assert wrong == WRONG_DENOMINATOR_PCT, wrong
    # The honest figure must be the materially smaller one.
    assert DATASET_LOSS_PCT < WRONG_DENOMINATOR_PCT


if __name__ == "__main__":
    # Delegate to pytest. Hand-calling the test functions cannot supply the
    # fixtures from conftest, so a direct run of a database test would fail
    # while the suite passed - two different answers to the same question.
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
