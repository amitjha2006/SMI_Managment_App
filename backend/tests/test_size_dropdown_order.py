"""Pack sizes render lightest-first everywhere.

Sizes were returned in insertion order, so a product whose 250g was entered
before its 50g showed "250g, 50g, 100g" in every pack-size dropdown. The
packaging_sizes relationship now orders by weight (then name), which fixes
production, inventory, products and challans in one place.

Run: python -m pytest backend/tests/test_size_dropdown_order.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402


def _req():
    from types import SimpleNamespace
    return SimpleNamespace(query_params={"reason": "test change note"}, headers={}, client=None)


def test_sizes_come_back_lightest_first_regardless_of_entry_order(db):
    prod = m.create_product(
        {
            "name": "Order Test Masala",
            "packaging_sizes": [
                {"size_name": "250g", "weight_in_kg": 0.25},
                {"size_name": "50g", "weight_in_kg": 0.05},
                {"size_name": "100g", "weight_in_kg": 0.1},
                {"size_name": "Loose", "weight_in_kg": 1.0},
            ],
        },
        _req(),
        db,
    )
    names = [s.size_name for s in prod.packaging_sizes]
    assert names == ["50g", "100g", "250g", "Loose Powder"], names


def test_equal_weights_tie_break_by_name(db):
    prod = m.create_product(
        {
            "name": "Tie Masala",
            "packaging_sizes": [
                {"size_name": "Loose Powder", "weight_in_kg": 1.0},
                {"size_name": "Loose", "weight_in_kg": 1.0},
            ],
        },
        _req(),
        db,
    )
    # both spellings merge into the one bulk row; nothing to tie-break
    assert [s.size_name for s in prod.packaging_sizes] == ["Loose Powder"]
