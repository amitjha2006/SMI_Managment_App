"""Pack-size filter narrows columns, not just rows.

Picking a pack size (e.g. 250g) must isolate that size's Stock/Sale/BAL
columns; "Loose" narrows like any other bucket now that loose powder rides
the same bucket engine instead of a standalone kg column. Filtering rows
while leaving every size column on the table answers nothing - and the
header already promises columns can hide ("Totals cover all sizes even when
columns are hidden"). The checkbox box under the dropdown extends this to
several sizes in one go; the dropdown stays as a one-pick shortcut.

Run: python -m pytest backend/tests/test_analysis_size_filter.py -v
"""

import os
import re


def _read():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "analysis.html"), encoding="utf-8") as fh:
        return fh.read()


def test_size_choice_narrows_bucket_columns():
    html = _read()
    assert re.search(r"visibleCols = v \? new Set\(\[v\]\) : null", html), (
        "picking a pack size never narrows the bucket columns to it"
    )


def test_size_box_allows_several_at_once():
    html = _read()
    assert 'id="an-sizes"' in html, "no multi-select checkbox box under the dropdown"
    assert "resetSizes" in html, "no Reset option for the size box"
    assert "selSizes.some" in html, "row filter still only honours one size"


def test_size_box_folds_after_selection():
    html = _read()
    assert "toggleSizesPanel" in html, "no fold toggle for the size box"
    assert ">Done<" in html, "no Done button to fold the box"
    assert "__anSizesDismissBound" in html, "no guarded outside-click/Escape dismiss"


def test_loose_choice_narrows_like_any_bucket():
    # Loose is a bucket column now, so the filter needs no loose branch:
    # one uniform rule narrows every choice, Loose included.
    html = _read()
    i = html.find("function applySizeColumns")
    assert i >= 0, "no single place applies the size dropdown to columns"
    assert "new Set([v])" in html, "size choice does not narrow to itself"
    body = html[i:i + 600]
    assert '"loose"' not in body, "loose still has its own filter branch"
