"""Automated tests for the stock & sales analysis engine.

1. Turmeric Powder rows from the confirmed reference report.
2. Turmeric Total Ready KG = 72.10 kg.
3. Negative BAL is preserved (Gram Flour 200g: BAL == -42, warning raised).
4. Gram Flour discrepancy documented: the simple formula yields 50.10 kg,
   NOT the reported 54.50 kg. If this test's second assertion ever fails
   because someone changed the formula to output 54.50, that change must be
   justified against the reference report — do NOT hardcode 54.50.
5. Mechanism proof: +22 packets of opening stock closes the gap exactly
   (BAL -20 -> total 54.50), showing the engine handles the missing component
   once it is supplied.
Run: python backend/tests/test_stock_analysis.py
"""

import os
import sys
from decimal import Decimal

# Runnable directly (`python backend/tests/test_stock_analysis.py`) as well as
# under pytest, so put the repo root on the path first.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app.services.stock_analysis import (  # noqa: E402
    SizeBucketInput,
    analyze_product,
    build_analysis,
    check_weight_matches_name,
    compute_size_bucket,
    normalize_size_bucket,
)


def _pkt(buckets, label):
    return buckets[label]


def test_turmeric_250g_bal():
    b = compute_size_bucket(
        SizeBucketInput(
            label="250g",
            pack_weight_kg=Decimal("0.25"),
            production_kg=Decimal("5860.5"),
            sale_packets=Decimal("23295"),
        )
    )
    # 5860.5 / 0.25 = 23442 stock
    assert b.stock_packets == 23442.0, b.stock_packets
    assert b.sale_packets == 23295.0
    assert b.bal_packets == 147.0, b.bal_packets
    assert b.bal_kg == 36.75, b.bal_kg
    assert b.warnings == []


def test_turmeric_100g_bal_zero():
    b = compute_size_bucket(
        SizeBucketInput(
            label="100g",
            pack_weight_kg=Decimal("0.1"),
            production_kg=Decimal("20.1"),
            sale_packets=Decimal("201"),
        )
    )
    assert b.stock_packets == 201.0
    assert b.bal_packets == 0.0
    assert b.warnings == []


def test_turmeric_50g_bal():
    b = compute_size_bucket(
        SizeBucketInput(
            label="50g",
            pack_weight_kg=Decimal("0.05"),
            production_kg=Decimal("807.6"),
            sale_packets=Decimal("15455"),
        )
    )
    # 807.6 / 0.05 = 16152 stock
    assert b.stock_packets == 16152.0, b.stock_packets
    assert b.bal_packets == 697.0, b.bal_packets
    assert b.bal_kg == 34.85, b.bal_kg


def test_turmeric_total_ready_kg():
    buckets = [
        SizeBucketInput(
            label="250g",
            pack_weight_kg=Decimal("0.25"),
            production_kg=Decimal("5860.5"),
            sale_packets=Decimal("23295"),
        ),
        SizeBucketInput(
            label="100g",
            pack_weight_kg=Decimal("0.1"),
            production_kg=Decimal("20.1"),
            sale_packets=Decimal("201"),
        ),
        SizeBucketInput(
            label="50g",
            pack_weight_kg=Decimal("0.05"),
            production_kg=Decimal("807.6"),
            sale_packets=Decimal("15455"),
        ),
    ]
    res = analyze_product(99, "Turmeric Powder", "Dry Masala", buckets, Decimal("0.5"))
    assert res.buckets["250g"].bal_packets == 147.0
    assert res.buckets["100g"].bal_packets == 0.0
    assert res.buckets["50g"].bal_packets == 697.0
    assert res.loose_kg == 0.5
    # 36.75 + 0 + 34.85 + 0.50 = 72.10
    assert res.total_ready_kg == 72.10, res.total_ready_kg
    assert res.warnings == []


def test_negative_bal_preserved_with_warning():
    b = compute_size_bucket(
        SizeBucketInput(
            label="200g",
            pack_weight_kg=Decimal("0.2"),
            production_kg=Decimal("232.6"),
            sale_packets=Decimal("1205"),
        )
    )
    # 232.6 / 0.2 = 1163 stock; 1163 - 1205 = -42 (must NOT be clamped)
    assert b.stock_packets == 1163.0, b.stock_packets
    assert b.bal_packets == -42.0, b.bal_packets
    assert any("negative BAL" in w for w in b.warnings), b.warnings


def test_gram_flour_baseline_without_additions():
    """Bare inputs (no addition components) yield 50.10 — the engine math."""
    res = analyze_product(
        100,
        "Gram Flour",
        None,
        [
            SizeBucketInput(
                label="200g",
                pack_weight_kg=Decimal("0.2"),
                production_kg=Decimal("232.6"),
                sale_packets=Decimal("1205"),
            )
        ],
        Decimal("58.5"),
    )
    assert res.total_ready_kg == 50.10, res.total_ready_kg


def test_gram_flour_resolved_with_opening():
    """RESOLVED: +22 packets (4.4 kg) opening -> BAL -20 -> 54.50 exactly."""
    res = analyze_product(
        100,
        "Gram Flour",
        None,
        [
            SizeBucketInput(
                label="200g",
                pack_weight_kg=Decimal("0.2"),
                production_kg=Decimal("232.6"),
                opening_kg=Decimal("4.4"),
                sale_packets=Decimal("1205"),
            )
        ],
        Decimal("58.5"),
    )
    assert res.buckets["200g"].bal_packets == -20.0
    assert res.total_ready_kg == 54.50, res.total_ready_kg


def test_reconciliation_cross_check():
    b = compute_size_bucket(
        SizeBucketInput(
            label="50g",
            pack_weight_kg=Decimal("0.05"),
            production_kg=Decimal("100"),
            sale_packets=Decimal("1000"),
            movement_sale_kg=Decimal("60"),
        )
    )
    # 100/0.05 = 2000 stock; movement implies 1200 sold vs 1000 in challans
    assert any("!=" in w for w in b.warnings), b.warnings


def test_normalize_size_bucket():
    assert normalize_size_bucket("50g") == 50
    assert normalize_size_bucket("100g") == 100
    assert normalize_size_bucket("100") == 100
    assert normalize_size_bucket("175g") == 175
    assert normalize_size_bucket("250g") == 250
    assert normalize_size_bucket("200g") == 200
    assert normalize_size_bucket("Loose") == "loose"
    assert normalize_size_bucket("Loose Powder") == "loose"
    assert normalize_size_bucket("???") is None
    assert normalize_size_bucket(None) is None


def test_every_row_has_identical_bucket_shape():
    """Regression: a new on-demand size (500g) gets its OWN column — never
    an 'Other' bucket — and every row carries every column.
    
    Canonical sizes only appear in columns if they exist in the data.
    """

    def sz(sid, name, w):
        return {
            "size_id": sid,
            "size_name": name,
            "weight_kg": w,
            "production_kg": 10,
            "opening_kg": 0,
            "adjustment_kg": 0,
            "return_kg": 0,
            "wastage_kg": 0,
            "extra_packets": 0,
            "sale_packets": 2,
            "movement_sale_kg": 1.0,
        }

    out = build_analysis(
        [
            {
                "product_id": 1,
                "product_name": "P1",
                "category": None,
                "sizes": [sz(1, "50g", 0.05)],
            },
            {
                "product_id": 2,
                "product_name": "P2",
                "category": None,
                "sizes": [sz(2, "500g", 0.5)],
            },
        ]
    )
    keys = [c["key"] for c in out["columns"]]
    # Only canonical sizes that appear in data are included (50g here)
    assert "50" in keys
    assert "500" in keys  # dynamic column, not "other"
    assert "other" not in keys
    assert all("other" not in r["buckets"] for r in out["rows"])
    for r in out["rows"]:
        for c in out["columns"]:
            assert c["key"] in r["buckets"], (r["product_name"], c["key"])
    p2 = [r for r in out["rows"] if r["product_name"] == "P2"][0]
    assert p2["buckets"]["500"]["bal_packets"] == 18.0  # 10/0.5 - 2
    p1 = [r for r in out["rows"] if r["product_name"] == "P1"][0]
    assert p1["buckets"]["500"]["bal_packets"] == 0.0


def test_weight_name_check_flags_bad_master():
    assert check_weight_matches_name("250g", 0.05) is not None  # implies 0.25
    assert check_weight_matches_name("100g", 0.1) is None
    assert check_weight_matches_name("100", 0.1) is None
    assert check_weight_matches_name("Loose", 1.5) is None


def _turmeric_product():
    def sz(sid, name, w, prod, sale, mov):
        return {
            "size_id": sid,
            "size_name": name,
            "weight_kg": w,
            "production_kg": prod,
            "opening_kg": 0,
            "adjustment_kg": 0,
            "return_kg": 0,
            "wastage_kg": 0,
            "extra_packets": 0,
            "sale_packets": sale,
            "movement_sale_kg": mov,
        }

    return {
        "product_id": 2,
        "product_name": "Turmeric Powder",
        "category": "Dry Masala",
        "sizes": [
            sz(1, "250g", 0.25, 5860.5, 23295, 5860.5 - 36.75),
            sz(2, "100g", 0.1, 20.1, 201, 20.1),
            sz(3, "50g", 0.05, 807.6, 15455, 807.6 - 34.85),
            # loose rows live in "sizes"; detected by name
            {
                "size_id": 9,
                "size_name": "Loose",
                "weight_kg": 1,
                "production_kg": 0.5,
                "opening_kg": 0,
                "adjustment_kg": 0,
                "return_kg": 0,
                "wastage_kg": 0,
                "extra_packets": 0,
                "sale_packets": 0,
                "movement_sale_kg": 0,
            },
        ],
    }


def test_build_analysis_turmeric_shape():
    out = build_analysis([_turmeric_product()])
    assert out["sizes"] == [250, 200, 175, 100, 50]
    assert out["has_other"] is False
    row = out["rows"][0]
    assert row["buckets"]["250"]["bal_packets"] == 147.0
    assert row["buckets"]["100"]["bal_packets"] == 0.0
    assert row["buckets"]["50"]["bal_packets"] == 697.0
    # Loose rides the same bucket engine now: 0.5 kg in, nothing sold.
    loose = row["buckets"]["loose"]
    assert loose["stock_packets"] == 0.5, loose
    assert loose["sale_packets"] == 0.0
    assert loose["bal_packets"] == 0.5
    assert row["loose_kg"] == 0
    assert row["total_ready_kg"] == 72.10, row["total_ready_kg"]
    assert row["warnings"] == []
    assert out["totals"]["total_ready_kg"] == 72.10
    assert "loose" in [c["key"] for c in out["columns"]]


def test_loose_bucket_tracks_stock_sale_bal():
    out = build_analysis([{
        "product_id": 3,
        "product_name": "Loose Masala",
        "category": None,
        "sizes": [{
            "size_id": 11,
            "size_name": "Loose Powder",
            "weight_kg": 1,
            "production_kg": 50,
            "opening_kg": 10,
            "adjustment_kg": 0,
            "return_kg": 0,
            "wastage_kg": 2,
            "extra_packets": 0,
            "sale_packets": 20,
            "movement_sale_kg": 20,
        }],
    }])
    row = out["rows"][0]
    loose = row["buckets"]["loose"]
    assert loose["stock_packets"] == 58.0, loose  # 50 + 10 - 2
    assert loose["sale_packets"] == 20.0
    assert loose["bal_packets"] == 38.0
    assert row["total_ready_kg"] == 38.0, row["total_ready_kg"]


def test_loose_missing_weight_means_one_kg():
    out = build_analysis([{
        "product_id": 4,
        "product_name": "Weightless Loose",
        "category": None,
        "sizes": [{
            "size_id": 12,
            "size_name": "Loose",
            "weight_kg": 0,
            "production_kg": 7.5,
            "opening_kg": 0,
            "adjustment_kg": 0,
            "return_kg": 0,
            "wastage_kg": 0,
            "extra_packets": 0,
            "sale_packets": 2.5,
            "movement_sale_kg": 2.5,
        }],
    }])
    loose = out["rows"][0]["buckets"]["loose"]
    assert loose["stock_packets"] == 7.5, loose
    assert loose["bal_packets"] == 5.0


def test_analysis_style_pins():
    # Stock/Sale/BAL read as header pills + accented summary cards, not
    # full-column pastel bands: white body cells, color only in the group
    # sub-headers, the BAL column, negatives, and the stat cards.
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "analysis.html"), encoding="utf-8") as fh:
        html = fh.read()
    for needle in ("pill-stock", "pill-sale", "pill-bal", "an-legend",
                   "stat-blue", "stat-red", "stat-amber"):
        assert needle in html, f"analysis styling missing: {needle}"


def test_analysis_suggestions_product_names_only():
    # The Search box suggests bare product names; size combos would duplicate
    # the Pack Size filter, so they must not come back.
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "analysis.html"), encoding="utf-8") as fh:
        html = fh.read()
    assert "suggestions.add(r.product_name" in html, "product names are not suggested"
    assert "`${pname} (`" not in html, "search suggests size combos despite the size filter"
    assert "`${pname} (Loose)`" not in html, "search suggests a Loose combo"


def test_route_lists_all_products_with_zero_balances(db):
    """The analysis route must list every active product (zero balances
    included) so the report is not blank when there are no transactions yet."""
    from backend.app.routes.sales import analysis_stock_sales
    from backend.app import models

    p1 = models.Product(product_id="P-1", name="Alpha Masala", is_active=True)
    p2 = models.Product(product_id="P-2", name="Beta Masala", is_active=True)
    db.add_all([p1, p2])
    db.flush()
    db.add_all([
        models.PackagingSize(product_id=p1.id, size_name="250g", weight_in_kg=0.25, is_active=True),
        models.PackagingSize(product_id=p2.id, size_name="100g", weight_in_kg=0.1, is_active=True),
    ])
    db.commit()

    out = analysis_stock_sales(from_date=None, to_date=None, product_id=None, db=db)
    ids = {r["product_id"] for r in out["rows"]}
    assert ids == {p1.id, p2.id}, ids
    for r in out["rows"]:
        for b in r["buckets"].values():
            assert b["stock_packets"] == 0.0, b
            assert b["sale_packets"] == 0.0, b
            assert b["bal_packets"] == 0.0, b


if __name__ == "__main__":
    # Delegate to pytest. Hand-calling the test functions cannot supply the
    # fixtures from conftest, so a direct run of a database test would fail
    # while the suite passed - two different answers to the same question.
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))