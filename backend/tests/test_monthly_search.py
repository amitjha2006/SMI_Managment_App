"""Monthly Product Sales search suggests products in a dropdown.

The Search Product box used to be a bare text filter: typing narrowed the
pivot columns but nothing ever listed what matched, so a product could only
be found by typing it exactly. The box now drops down the matching products
from the loaded year; picking one fills the box and filters the pivot.

Run: python -m pytest backend/tests/test_monthly_search.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _read():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(
        os.path.join(root, "frontend", "pages", "monthly-sales.html"), encoding="utf-8"
    ) as fh:
        return fh.read()


def test_suggestion_box_exists():
    html = _read()
    assert 'id="ms-suggest"' in html, "no suggestion dropdown under the product search"


def test_typing_renders_suggestions():
    html = _read()
    assert "renderSuggest" in html, "typing never lists matching products"
    i = html.find("function renderSuggest()")
    assert i >= 0
    body = html[i:i + 1500]
    assert "pivotData.products" in body, "suggestions are not fed by the loaded products"


def test_picking_a_suggestion_filters():
    html = _read()
    assert "pickSuggest" in html, "a suggestion cannot be picked"
    i = html.find("pickSuggest = function")
    assert i >= 0
    body = html[i:i + 500]
    assert "renderPivot" in body, "picking a suggestion does not filter the pivot"


def test_dropdown_hides_cleanly():
    html = _read()
    assert "hideSuggest" in html, "suggestion box never closes on Escape/blur"


def test_dropdown_not_shown_on_load():
    # The dropdown must never open uninvited: data loads must not trigger
    # suggestions. It appears on focus/click and while typing only.
    html = _read()
    i = html.find("async function loadPivot()")
    assert i >= 0
    body = html[i:i + 800]
    assert "renderSuggest" not in body, "loading data pops the dropdown open by default"


def test_dropdown_shows_on_focus():
    # Clicking the box lists products for browsing; typing filters them.
    html = _read()
    assert 'onfocus="renderSuggest();"' in html, "clicking the search box lists nothing"
