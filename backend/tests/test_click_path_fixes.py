"""Pins for the Expenses/Production click-path audit findings CP-005..CP-011.

Each test names the user-visible failure it prevents, because the fix lives in
page-local JS that no Python test can execute - the pin keeps the shape of the
fix from being refactored away.

Run: python -m pytest backend/tests/test_click_path_fixes.py -v
"""

import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(*parts):
    with open(os.path.join(_ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def _fn(src, name):
    """Return the body of `function name(...) {...}` / `name = function () {...}`."""
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


def test_cp005_new_category_wins_over_picked_one():
    """Typing "Or New Category" while a category is picked filed the expense
    under the picked one: the backend prefers category_id, which was sent."""
    body = _fn(_read("frontend", "pages", "expenses.html"), "saveExpense")
    assert "newCat || !pickedCat ? null : parseInt(pickedCat)" in body, (
        "saveExpense must null the category_id when a new name is typed"
    )
    assert "category_name: newCat || null" in body, "typed name no longer sent"


def test_cp006_search_covers_every_pack_size():
    """Searching a size that was not the entry's first one returned nothing."""
    body = _fn(_read("frontend", "pages", "production.html"), "filterProduction")
    assert "e.outputs" in body and "packaging_size_name" in body, (
        "filterProduction must search the entry's outputs, not only its first size"
    )


def test_cp007_formulation_guide_ignores_stale_response():
    """Picking product A then B quickly could leave A's recipe (and its Apply
    button) on screen for B."""
    body = _fn(_read("frontend", "pages", "production.html"), "refreshFormulationGuide")
    guard = body.find("!== pid) return;")
    await_at = body.find('await window.UI.api("/products/" + pid')
    assert await_at != -1, "formulation fetch moved"
    assert guard > await_at, "no product-changed guard after the formulation fetch"


def test_cp008_detail_card_ignores_stale_response():
    """Clicking View on two entries quickly let the slower response overwrite
    the newer detail card."""
    body = _fn(_read("frontend", "pages", "production.html"), "viewProduction")
    assert "detailToken" in body, "viewProduction lost its request token"
    assert body.count("token !== detailToken") >= 2, (
        "both awaits (entry + materials) need a staleness guard"
    )


def test_cp009_quick_add_errors_reset_message_colour():
    """A validation error reused the colour of the previous save, so a plain
    "type a name" warning could look like a success."""
    body = _fn(_read("frontend", "pages", "production.html"), "saveQuickAddProduct")
    assert "var(--color-danger)" in body, "quick-add errors no longer set the colour"
    assert body.count("fail(") >= 3, "every validation branch must route through fail()"


def test_cp010_category_list_refreshes_without_wiping_a_pick():
    """The list must refresh on every open (a category created since the page
    loaded has to appear), while a pick made during the fetch must survive."""
    html = _read("frontend", "pages", "expenses.html")
    body = _fn(html, "openNewExpenseForm")
    assert "loadCategories();" in body, "openNewExpenseForm no longer refreshes categories"
    assert "options.length" not in body, (
        "categories must reload on every open - loading only when empty left a "
        "newly created category invisible until a full page reload"
    )
    loader = _fn(html, "loadCategories")
    assert "const keep = sel.value" in loader and "sel.value = keep" in loader, (
        "loadCategories must restore the pick it saw when the fetch was sent"
    )


def test_cp011_production_form_validation_message_is_reachable():
    """`required` on the product select made the browser block submit, so the
    in-form "Select a product…" message could never appear."""
    html = _read("frontend", "pages", "production.html")
    tag = re.search(r'<select id="pe-product"[^>]*>', html)
    assert tag, "product select moved"
    assert "required" not in tag.group(0), (
        "pe-product must not be required - the save handler owns that message"
    )
    assert "Select a product first." in html


# --- same stale-picker shape as CP-010, elsewhere in the app -----------------

def test_pickers_refresh_every_time_their_form_opens():
    """A picker filled once at page load never shows a customer / supplier /
    product added on another page later in the session."""
    cases = [
        ("challans.html", "openNewChallanForm", ["await loadCustomers()", "await loadProducts()"]),
        ("invoices.html", "openNewInvoiceForm", ["await loadDropdowns()"]),
        ("production.html", "openNewProductionForm", ["loadProducts()"]),
    ]
    for page, fn, calls in cases:
        body = _fn(_read("frontend", "pages", page), fn)
        for call in calls:
            assert call in body, f"{page}: {fn}() no longer refreshes via {call}"
        assert "length) await load" not in body, (
            f"{page}: {fn}() still loads only when the list is empty"
        )
        assert "length || !" not in body, (
            f"{page}: {fn}() still loads only when the lists are empty"
        )


def test_production_picker_refresh_keeps_the_pick():
    body = _fn(_read("frontend", "pages", "production.html"), "renderProductionProductOptions")
    assert "sel.value = keep" in body, (
        "a background product refresh must not reset the picker to Select product…"
    )


def test_recipe_rows_refresh_ingredients_when_the_editor_opens():
    """A raw material added on the Raw Materials page has to be selectable as a
    recipe ingredient without reloading the Products page."""
    body = _fn(_read("frontend", "pages", "inventory.html"), "loadFormulation")
    assert "await loadRawNames()" in body, "recipe rows still use the page-load ingredient list"
