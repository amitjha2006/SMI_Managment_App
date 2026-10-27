"""Every sortable date column sorts by timestamp, app-wide.

The shared digit-strip sorter mangles formatted dates ("10/4/2026" sorts
below "9/28/2026"; ISO datetimes collapse to their year), so any date cell
rendered as text silently ties or misorders. Every renderTable with a date
cell therefore carries a hidden ISO key plus a sortValue timestamp hook
(shared UI.tsKey), like the reports and recent-transactions tables do.

Run: python -m pytest backend/tests/test_date_sort_audit.py -v
"""

import os

# Page -> (column key holding the display date). Each must be wired with a
# hidden ISO field and a sortValue timestamp hook.
DATE_COLUMNS = {
    "frontend/pages/backup.html": ["date"],
    "frontend/pages/expenses.html": ["date"],
    "frontend/pages/audit.html": ["at"],
    "frontend/pages/alerts.html": ["at"],
    "frontend/pages/challans.html": ["date"],
    "frontend/pages/invoices.html": ["date"],
    "frontend/pages/inventory.html": ["date"],
    "frontend/pages/production.html": ["date"],
    "frontend/pages/settings.html": ["at"],
}


def _read(rel):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


def test_shared_timestamp_key_exists():
    js = _read("frontend/js/components.js")
    assert "tsKey" in js, "no shared timestamp key for date sorting"


def test_every_date_column_sorts_by_timestamp():
    import re

    failures = []
    for page, keys in DATE_COLUMNS.items():
        html = _read(page)
        for key in keys:
            wired = re.search(
                r"sortValue\s*:\s*\{[^}]*\b" + re.escape(key) + r"\b\s*:",
                html,
            )
            if not wired:
                failures.append(f"{page} column '{key}'")
    assert not failures, (
        "date columns sorting by display text instead of timestamp:\n"
        + "\n".join(failures)
    )
