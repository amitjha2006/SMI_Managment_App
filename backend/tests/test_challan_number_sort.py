"""Challan Number header sort: every click must reorder, both directions.

Root cause it pins: the shared sortRows() strips every non-numeric char, so
"CH/26-27/0001" collapses to 26 and every FY challan ties — ascending and
descending then produce the identical order and the second header click
appears dead. The table therefore needs a per-column sort value that is
FY-aware (options.sortValue), wired to the challan Number column.

Run: python -m pytest backend/tests/test_challan_number_sort.py -v
"""

import re
import sys

ROOT = r"C:\Shresth_Masala_Application"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _read(rel):
    with open(ROOT + "\\" + rel.replace("/", "\\"), encoding="utf-8") as fh:
        return fh.read()


def test_table_supports_per_column_sort_values():
    js = _read("frontend/js/components.js")
    assert "sortValue" in js, (
        "renderTable must accept options.sortValue so a column can sort by "
        "a computed value instead of its display text"
    )
    i = js.find("function sortRows")
    assert i >= 0
    assert re.search(r"valFn|sortVal|valueFn", js[i:i + 900]), (
        "sortRows must accept a value extractor for FY-style numbers"
    )


def test_challan_number_column_sorts_fy_aware():
    html = _read("frontend/pages/challans.html")
    assert "window.UI.docNumKey" in html, (
        "challans page must use the shared FY-aware number key"
    )
    assert re.search(r"sortValue\s*:\s*\{\s*number\s*:", html), (
        "challan table must wire the Number column to the FY-aware key"
    )
    js = _read("frontend/js/components.js")
    i = js.find("function docNumKey")
    assert i >= 0
    body = js[i:i + 600]
    assert "1000000" in body, (
        "the key must weight by FY (not just trailing digits)"
    )
