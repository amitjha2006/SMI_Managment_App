"""Date sort keys carry recorded time, not just the business date.

The universal date rule: same-day rows order newest-first (7pm, 4pm,
10am), so every date sort key must combine the business date with the
recorded entry time. A key built from the business date alone ties all
same-day rows at midnight and the click-sort keeps whatever order the
rows arrived in - ascending then shows 7pm first, which is backwards.

Pages whose display date is user-picked (challan/expense/invoice/
production/txn dates) therefore build their key as
`UI.tsKey(businessDate, createdAt)`. System-stamped dates (backup,
audit, alerts, shares, adjustments) already carry full precision in one
field and need only the single-argument form.

Run: python -m pytest backend/tests/test_timestamp_sort.py -v
"""

import os
import re

# Page -> business-date field whose rows also carry a recorded entry time.
SPLIT_DATE_PAGES = {
    "frontend/pages/challans.html": "date",
    "frontend/pages/expenses.html": "date",
    "frontend/pages/invoices.html": "date",
    "frontend/pages/production.html": "date",
    "frontend/pages/dashboard.html": "date",
}


def _read(rel):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


def test_tskey_overlays_entry_time():
    js = _read("frontend/js/components.js")
    m = re.search(r"function tsKey\(([^)]*)\)", js)
    assert m, "shared UI.tsKey helper missing"
    assert len([p for p in m.group(1).split(",") if p.strip()]) >= 2, (
        "UI.tsKey must accept the entry time as well as the business date"
    )


def test_split_date_pages_key_by_business_date_and_entry_time():
    failures = []
    for page in SPLIT_DATE_PAGES:
        html = _read(page)
        if not re.search(r"tsKey\([^,()\n]+,[^()\n]+\)", html):
            failures.append(page)
    assert not failures, (
        "date keys built from the business date alone tie every same-day row:\n"
        + "\n".join(failures)
    )
