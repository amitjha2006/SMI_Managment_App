"""The production form follows the batch, and fills its own stage chain.

Two complaints, one page. The form listed product and packaging first and the
raw materials near the bottom, while the batch itself starts with what comes
out of the store. And every stage input had to be retyped from the stage
before it, so the same weight was entered up to four times.

Now: Raw Materials -> Product -> Stages -> Summary, the first stage's input is
the materials total, and each later stage's input follows the previous stage's
output (a hand-typed figure still wins). Pack sizes and wrappers are recorded
at packing time, not on this form.

Run: python -m pytest backend/tests/test_production_form_layout.py -v
"""

import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PAGE = os.path.join(_ROOT, "frontend", "pages", "production.html")


def _html():
    with open(_PAGE, encoding="utf-8") as fh:
        return fh.read()


def _js():
    return "\n".join(re.findall(r"<script>(.*?)</script>", _html(), re.S))


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


def test_sections_follow_the_batch_order():
    html = _html()
    order = [
        'id="pe-materials"',
        'id="pe-product"',
        'id="pe-stages"',
        'id="pe-summary"',
    ]
    where = [html.index(marker) for marker in order]
    assert where == sorted(where), (
        "the form no longer reads Raw Materials -> Product -> Stages -> "
        f"Summary: {dict(zip(order, where, strict=True))}"
    )


def test_the_first_stage_input_comes_from_the_materials_total():
    fn = _fn(_js(), "syncStageInputs")
    assert "materialTotalKg()" in fn, "the first stage input no longer follows the materials"
    assert ".pe-in" in fn and "dataset.auto" in fn, (
        "a hand-typed stage input must still win over the derived one"
    )
    assert "syncStageInputs()" in _fn(_js(), "refreshDerived"), (
        "refreshDerived no longer re-runs the stage chain"
    )


def test_material_totals_ignore_wrapper_rows():
    # Wrappers are packaging material: counted at packing time, not on this form.
    fn = _fn(_js(), "materialTotalKg")
    assert ".pe-mat-row" in fn and "pe-wrap" not in fn


def test_editing_materials_redraws_the_chain():
    js = _js()
    assert "refreshProductionDerived" in _fn(js, "addMaterialRow"), (
        "a new material row no longer moves the first stage input"
    )
    assert "refreshDerived()" in _fn(js, "applyFormulation"), (
        "applying the formulation leaves the stage chain stale"
    )
    assert "refreshDerived()" in _fn(js, "refreshStageRows"), (
        "a product change rebuilds the stages without reseeding their inputs"
    )


def test_messages_are_above_the_form():
    html = _html()
    assert html.index('id="pe-msg"') < html.index('<form onsubmit="saveProduction'), (
        "Apply formulation… reports below the fold again"
    )


def test_a_new_entry_starts_at_the_first_material_row():
    body = _fn(_js(), "openNewProductionForm")
    assert "#pe-materials .pe-mat" in body and ".focus()" in body
