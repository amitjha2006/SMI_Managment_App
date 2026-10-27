"""Compact boxes on every page, not just the dashboard.

Cards and KPI stat boxes were roomy (24px card padding, 28px values);
on dense working pages that pushes content below the fold. Modals keep
their padding — dialogs need the air.

Run: python -m pytest backend/tests/test_compact_cards.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _css():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "css", "style.css"), encoding="utf-8") as fh:
        return fh.read()


def test_cards_are_compact():
    css = _css()
    i = css.find(".card {")
    assert i >= 0
    assert "padding: 16px;" in css[i:i + 300], "cards still roomy"


def test_stat_boxes_are_compact():
    css = _css()
    i = css.find(".stat-card {")
    assert i >= 0
    assert "padding: 14px;" in css[i:i + 300], "stat boxes still roomy"
    v = css.find(".stat-card .stat-value {")
    assert v >= 0
    assert "font-size: 24px;" in css[v:v + 200], "stat values still large"
