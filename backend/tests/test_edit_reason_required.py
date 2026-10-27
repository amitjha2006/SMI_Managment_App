"""Mandatory change notes: only money-bearing endpoints require a why note.

Policy (per the build queue): change notes are required only where money is
directly involved - customer/supplier payments, cash movements, expenses and
salary payments. Master data, business documents, settings and reports no
longer ask for a note; their audit trail still records who and when.

Creates stay exempt (building a challan or recording a payment is normal
work), as does the import flow.

This file is the house-audit style test like test_date_sort_audit.py: it
scans backend/main.py's route handlers directly, so a money endpoint added
tomorrow WITHOUT the reason check fails here by name instead of shipping
silently, and a non-money endpoint that gains a note also fails.

Run: python -m pytest backend/tests/test_edit_reason_required.py -v
"""

import os
import re
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

MAIN = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "backend",
    "main.py",
)

# Money-bearing endpoints: the only ones that require a change note.
_MONEY_PUT_PATCH = {"/payments/{payment_id}"}
_MONEY_STATUS_POSTS = [
    "/customers/{customer_id}/cash/{movement_id}/void",
    "/suppliers/{supplier_id}/cash/{movement_id}/void",
    "/payments/{payment_id}/restore",
    "/supplier-payments/{payment_id}/restore",
    "/expenses/{expense_id}/restore",
]
_MONEY_DELETES = [
    "/payments/{payment_id}",
    "/supplier-payments/{payment_id}",
    "/expenses/{expense_id}",
    "/payroll/payments/{payment_id}",
]


def _handlers():
    """All route handlers as (method, path, body) tuples.

    Body runs from the handler's `def` line to the next route or middleware
    decorator, so helper functions and middleware between routes are not
    wrongly attached to the preceding handler.
    """
    with open(MAIN, encoding="utf-8") as fh:
        src = fh.read()
    # Boundaries: any @app.(get|post|put|patch|delete|middleware) decorator.
    marks = list(
        re.finditer(
            r"@app\.(get|post|put|patch|delete|middleware)\(r?\"([^\"]+)\"", src
        )
    )
    out = []
    for i, mk in enumerate(marks):
        if mk.group(1) not in ("get", "post", "put", "patch", "delete"):
            continue  # middleware is a boundary only
        start = mk.end()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(src)
        body = src[start:end]
        out.append((mk.group(1), mk.group(2), body))
    return out


def _money_note_handlers():
    """Expected set of (method, path) that require a change note."""
    expected = set()
    for path in _MONEY_PUT_PATCH:
        expected.add(("put", path))
    for path in _MONEY_STATUS_POSTS:
        expected.add(("post", path))
    for path in _MONEY_DELETES:
        expected.add(("delete", path))
    return expected


def _actual_note_handlers():
    """Handlers whose body calls _change_reason."""
    handlers = _handlers()
    return {
        (method, path)
        for method, path, body in handlers
        if "_change_reason(" in body
    }


def test_only_money_endpoints_require_change_note():
    actual = _actual_note_handlers()
    expected = _money_note_handlers()
    missing = expected - actual
    extra = actual - expected
    assert not missing, (
        "Money endpoints missing the change-note check (_change_reason): "
        + "; ".join(f"{m.upper()} {p}" for m, p in sorted(missing))
    )
    assert not extra, (
        "Non-money endpoints must not require a change note: "
        + "; ".join(f"{m.upper()} {p}" for m, p in sorted(extra))
    )


def test_archive_and_restore_helpers_no_longer_ask_for_note():
    """The chokepoint helpers no longer request a note; call sites do."""
    with open(MAIN, encoding="utf-8") as fh:
        src = fh.read()
    for helper in ("def _archive_record(", "def _restore_record("):
        i = src.find(helper)
        assert i >= 0, helper
        body = src[i : i + 2500]
        assert "_change_reason(request)" not in body, (
            f"{helper} should not require a note itself"
        )
