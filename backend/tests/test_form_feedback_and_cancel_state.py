"""Two form-state bugs found by a click-path audit of Production and Expenses.

1. The wrapper quantities of a production entry live outside the DOM
   (wrapEdited / manualWrapRows), so form.reset() never cleared them. Cancelling
   an entry and starting the next one re-rendered the abandoned roll rows and
   posted their kg against the new entry - a silent stock deduction.

2. #pe-msg and #exp-msg sit INSIDE the form containers that the save handlers
   hide in the same tick, so "Entry X saved" and the "No wrapper deducted for
   <size>" warning were never painted. Archive/Restore wrote there too.

Run: python -m pytest backend/tests/test_form_feedback_and_cancel_state.py -v
"""

import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(*parts):
    with open(os.path.join(_ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def _fn(src, name):
    """Body of `function name(...) {` or `window.name = [async] function (...) {`."""
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


def test_cancel_clears_wrapper_state():
    html = _read("frontend", "pages", "production.html")
    # Wrapper state is computed server-side at pack time now; the form no longer
    # tracks wrapEdited / manualWrapRows outside the DOM, so there is nothing
    # for cancel to clear.
    assert "resetWrapperState" not in html, "old wrapper reset helper should be gone"


def test_wrapper_reset_clears_every_outside_dom_store():
    html = _read("frontend", "pages", "production.html")
    assert "wrapEdited" not in html and "manualWrapRows" not in html, (
        "outside-DOM wrapper stores removed from the form"
    )


def test_production_save_reports_through_a_toast():
    html = _read("frontend", "pages", "production.html")
    body = _fn(html, "saveProduction")
    assert "UI.notify(" in body, (
        "save confirmation must be a toast - #pe-msg is hidden with the form"
    )
    assert "No wrapper deducted" not in body, "wrapper warnings removed from save"
    assert not re.search(r'msg\.textContent\s*=\s*"Entry ', body), (
        "success message written into the hidden #pe-msg again"
    )


def test_archive_and_restore_use_toasts():
    prod = _read("frontend", "pages", "production.html")
    exp = _read("frontend", "pages", "expenses.html")
    for src, names in (
        (prod, ("deleteProduction", "restoreProduction")),
        (exp, ("deleteExpense", "restoreExpense")),
    ):
        for name in names:
            body = _fn(src, name)
            assert "UI.notify(" in body, f"{name}() must report via UI.notify"
            assert "msg.textContent" not in body, (
                f"{name}() still writes the hidden #*-msg container"
            )


def test_expense_save_reports_through_a_toast():
    html = _read("frontend", "pages", "expenses.html")
    body = _fn(html, "saveExpense")
    assert "UI.notify(" in body, (
        "expense save confirmation must be a toast - #exp-msg is hidden with "
        "the form"
    )
    assert "Expense saved under" in body
