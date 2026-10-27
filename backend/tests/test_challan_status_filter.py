"""Challans list status filter: draft/confirmed/paid/closed at a glance.

Daily work thinks in statuses, so the list needs a Status dropdown next to
the sort control. It composes with search/dates/FY/overdue and persists
with the other saved filters.

Run: python -m pytest backend/tests/test_challan_status_filter.py -v
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _read():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "challans.html"), encoding="utf-8") as fh:
        return fh.read()


def test_status_dropdown_offers_every_status():
    html = _read()
    m = re.search(r'<select id="challan-status"[^>]*>(.*?)</select>', html, re.S)
    assert m, "no Status dropdown on the challans list"
    opts = m.group(1)
    for value in ("draft", "confirmed", "partially_paid", "paid", "closed"):
        assert f'value="{value}"' in opts, f"Status dropdown missing {value}"


def test_filter_reads_the_status_dropdown():
    html = _read()
    i = html.find("window.filterChallans = function () {")
    assert i >= 0
    body = html[i:i + 1500]
    assert "challan-status" in body, "filterChallans ignores the Status dropdown"


def test_status_choice_persists_like_other_filters():
    html = _read()
    assert re.search(r"status:\s*document\.getElementById\(\"challan-status\"\)\.value", html), (
        "status choice is not saved with the other filters"
    )
    assert 's.status' in html or "typeof s.status" in html, (
        "saved status choice is not restored"
    )
