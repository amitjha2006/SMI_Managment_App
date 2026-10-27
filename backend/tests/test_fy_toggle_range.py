"""The "Current FY only" checkbox must not leave an impossible date range.

Click-path audit finding CP-004: every list page's FY toggle set only the
`from` box to the FY start, leaving whatever `to` was there (usually last FY's
saved end date). inDateRange() then rejects every row - from 2026-04-01 to
2026-03-31 matches nothing - so the checkbox looked broken and the list read
"Showing 0 of N".

Pin: the preset lives in one helper (UI.applyFyStart) that sets BOTH bounds,
every toggle routes through it, and restoring saved filter state drops an
inverted upper bound instead of resurrecting the empty list on reload.

Run: python -m pytest backend/tests/test_fy_toggle_range.py -v
"""

import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# page -> (toggle fn, from id, to id, restore fn)
_PAGES = {
    "expenses.html": ("toggleExpFy", "exp-from", "exp-to", "restoreExpenseFilters"),
    "production.html": ("togglePeFy", "pe-from", "pe-to", "restoreProductionFilters"),
    "challans.html": ("toggleChallanFy", "challan-from", "challan-to", "restoreChallanFilters"),
    "invoices.html": ("toggleInvFy", "inv-from", "inv-to", "restoreInvoiceFilters"),
}


def _read(*parts):
    with open(os.path.join(_ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def _fn(src, name):
    n = re.escape(name)
    m = re.search(
        r"(?:function\s+" + n + r"\s*\([^)]*\)\s*\{"
        r"|(?:window\.)?" + n + r"\s*=\s*(?:async\s+)?function\s*\([^)]*\)\s*\{)",
        src,
    )
    assert m, f"{name}() moved"
    depth, i = 1, m.end()
    while i < len(src) and depth:
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        i += 1
    return src[m.end():i]


def test_helper_sets_both_bounds():
    js = _read("frontend", "js", "components.js")
    body = _fn(js, "applyFyStart")
    assert "fyStartStr()" in body, "preset no longer derives from the FY start"
    assert 'to.value = ""' in body, (
        "preset leaves a stale upper bound - from > to filters every row out"
    )
    assert "applyFyStart" in js[js.find("return { notify"):], "helper not exported"


def test_restore_drops_inverted_range():
    js = _read("frontend", "js", "components.js")
    body = _fn(js, "dropInvertedRange")
    assert "to.value < from.value" in body, (
        "restore guard must compare the two bounds"
    )
    assert "dropInvertedRange" in js[js.find("return { notify"):], (
        "restore guard not exported"
    )


def test_every_page_toggle_uses_the_preset():
    for page, (toggle, from_id, to_id, restore) in _PAGES.items():
        html = _read("frontend", "pages", page)
        body = _fn(html, toggle)
        assert f'applyFyStart("{from_id}", "{to_id}")' in body, (
            f"{page}: {toggle}() must preset both bounds via UI.applyFyStart"
        )
        assert "fyStartStr()" not in body, (
            f"{page}: {toggle}() still sets only the lower bound"
        )
        rbody = _fn(html, restore)
        assert f'dropInvertedRange("{from_id}", "{to_id}")' in rbody, (
            f"{page}: {restore}() can restore an empty date range"
        )


def test_dashboard_toggle_uses_the_preset():
    html = _read("frontend", "pages", "dashboard.html")
    body = _fn(html, "toggleTxnFy")
    assert 'applyFyStart("txn-from", "txn-to")' in body
    rbody = _fn(html, "restoreTxnFilters")
    assert 'dropInvertedRange("txn-from", "txn-to")' in rbody
