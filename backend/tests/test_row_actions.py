"""Row action buttons: one shared one-line layout on every table.

Every Actions cell must use the shared .row-actions class (single-line flex
row, sideways scroll via .table-wrap) instead of space-joined buttons in a
plain cell, which wrap mid-row. Tables must size to their content so row
colors (header tint, hover, archived) run edge-to-edge through the scroll
region instead of ending at the viewport edge.

Run: python -m pytest backend/tests/test_row_actions.py -v
"""

import re
import sys

ROOT = r"C:\Shresth_Masala_Application"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _read(rel):
    with open(ROOT + "\\" + rel.replace("/", "\\"), encoding="utf-8") as fh:
        return fh.read()


PAGES_WITH_ACTIONS = (
    "frontend/pages/challans.html",
    "frontend/pages/invoices.html",
    "frontend/pages/backup.html",
    "frontend/pages/inventory.html",
    "frontend/pages/users.html",
    "frontend/pages/production.html",
    "frontend/pages/expenses.html",
    "frontend/pages/customers.html",
    "frontend/pages/employees.html",
    "frontend/pages/suppliers.html",
    "frontend/pages/settings.html",
)


def test_shared_row_actions_class_exists():
    css = _read("frontend/css/style.css")
    m = re.search(r"\.row-actions\s*\{([^}]*)\}", css)
    assert m, ".row-actions is not defined in style.css"
    body = m.group(1)
    assert "inline-flex" in body, ".row-actions must be an inline-flex row"
    assert re.search(r"flex-wrap\s*:\s*wrap", body), (
        ".row-actions must wrap into two lines so no sideways scroll is needed"
    )
    assert not re.search(r"flex-wrap\s*:\s*nowrap", body), (
        ".row-actions must not force a single line"
    )


def test_every_actions_cell_uses_the_shared_class():
    missing = [p for p in PAGES_WITH_ACTIONS if "row-actions" not in _read(p)]
    assert not missing, (
        "these pages build Actions cells without the shared .row-actions "
        f"class: {missing}"
    )


def test_no_bare_space_joined_action_buttons():
    # Two buttons separated by only a space inside a cell wrap mid-row.
    bad = []
    for p in PAGES_WITH_ACTIONS:
        for m in re.finditer(r"</button> <button", _read(p)):
            # Allowed only inside the shared wrapper, which uses gap instead.
            start = max(0, m.start() - 400)
            if "row-actions" not in _read(p)[start:m.start()]:
                bad.append(f"{p} at offset {m.start()}")
                break
    assert not bad, f"space-joined action buttons outside .row-actions: {bad}"


def test_table_spans_its_content_when_scrolling():
    css = _read("frontend/css/style.css")
    m = re.search(r"\.table-wrap table\s*\{([^}]*)\}", css)
    assert m, ".table-wrap table rule not found"
    body = m.group(1)
    # The table must be constrained to the viewport (width: 100%) so action
    # cells have something to wrap against. width: max-content forces the
    # table to its full unwrapped width, which defeats flex wrapping and
    # brings back the sideways scroll.
    assert "max-content" not in body, (
        "table must not use max-content: it stops .row-actions from wrapping"
    )
    assert re.search(r"width\s*:\s*100%", body), (
        "table must fill but not exceed the viewport width"
    )
