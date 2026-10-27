"""New Purchase Invoice form: clean Rate box and room for the line items.

Two operator complaints, both pinned here:

* Picking a material parked ``0.00`` in the Rate box. Materials with no saved
  rate come back from the API as the *string* ``"0.00"``, which is truthy, so
  the old ``if (opt.dataset.rate)`` guard always fired. Only a real rate
  (parseable, greater than zero) may be prefilled.
* The header pushed the line items â€” and the Save button â€” below the window.
  The form now keeps one view on screen at a time and caps the line-item list
  to a height measured from the live layout.

Run: python -m pytest backend/tests/test_purchase_invoice_form_ux.py -v
"""

import os


def _read():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "invoices.html"), encoding="utf-8") as fh:
        return fh.read()


def _block(html, marker, size=1200):
    i = html.find(marker)
    assert i >= 0, f"{marker!r} not found in invoices.html"
    return html[i:i + size]


# --- Rate box stays clean -------------------------------------------------


def test_zero_rate_is_not_prefilled():
    """'0.00' is truthy, so the guard must compare numbers, not strings."""
    block = _block(_read(), "matSel.addEventListener", 900)
    assert "parseFloat(raw)" in block, "rate is not parsed to a number"
    assert "rate > 0" in block, "rate is still guarded by truthiness"
    assert "if (opt && opt.dataset.rate)" not in block, (
        "truthiness guard is back: a '0.00' rate would fill the box again"
    )


def test_real_rate_is_still_prefilled_verbatim():
    """A genuine rate keeps the API's own formatting (e.g. 120.00)."""
    block = _block(_read(), "matSel.addEventListener", 900)
    assert "(isFinite(rate) && rate > 0) ? raw : \"\"" in block, (
        "a real rate must be copied verbatim, anything else must clear the box"
    )


# --- One view at a time ---------------------------------------------------


def test_list_steps_aside_while_the_form_is_open():
    html = _read()
    show = "setInvoiceListVisible(true)"
    assert "setInvoiceListVisible(false)" in _block(
        html, "window.openNewInvoiceForm = async function", 1400
    )
    # Every exit from the form must bring the list back.
    assert show in _block(html, "window.cancelInvoiceForm = function", 500)
    assert show in _block(html, "window.saveInvoice = async function", 4000)
    assert show in _block(html, "window.backToInvoiceList = function", 500)


def test_the_page_title_is_not_repeated_on_the_card():
    """The top header already names the page; the card title was dead height."""
    html = _read()
    assert "Purchase Invoices</h2>" not in html
    assert "New Purchase Invoice</h4>" not in html


def test_tabs_stay_outside_the_purchase_wrap():
    """switchInvoiceTab hides #inv-purchase-wrap, so tabs must live outside it."""
    html = _read()
    assert html.index('id="inv-tab-purchase"') < html.index('id="inv-purchase-wrap"')


def test_purchase_actions_hide_with_the_sales_tab():
    body = _block(_read(), "window.switchInvoiceTab = function", 900)
    assert "inv-purchase-actions" in body, (
        "Show archived / + New stay visible on the Sales tab"
    )


# --- The line-item list owns its own scroll -------------------------------


def test_line_item_list_scrolls_instead_of_pushing_the_footer_away():
    html = _read()
    assert 'id="inv-items-wrap"' in html
    assert "#inv-items-wrap { overflow-y: auto; }" in html
    assert 'id="inv-items-tail"' in html, "footer block that must stay visible is missing"


def test_list_height_is_measured_from_the_live_layout():
    html = _read()
    body = _block(html, "window.fitInvoiceItems = function", 900)
    assert "window.innerHeight" in body, "height is hard-coded instead of measured"
    assert "getBoundingClientRect" in body
    assert "maxHeight" in body
    assert 'addEventListener("resize"' in html, "list height is not re-measured on resize"


# --- The Material column gets the room ------------------------------------
#
# UI.quickSelect hides the <select> and puts a search box plus a 32px toggle in
# its place, with the dropdown pinned to the column (left:0; right:0). A narrow
# column therefore squeezes both the typing box and the dropdown list, which is
# what clipped long material names.


def test_material_column_claims_the_row_slack():
    html = _read()
    assert 'style="flex:2.4; min-width:240px;"><label>Material</label>' in html, (
        "Material column lost its widened share of the line-item row"
    )


def test_numeric_cells_stay_wide_enough_for_a_large_figure():
    """The money read-outs never wrap, so their columns must not be starved."""
    html = _read()
    for spec in ('max-width:76px;"><label>Qty', 'max-width:104px;"><label>Rate',
                 'max-width:64px;"><label>GST %', 'max-width:90px;"><label>Transport',
                 'max-width:90px;"><label>Other', 'max-width:100px; min-width:100px;'):
        assert spec in html, f"line-item column changed unexpectedly: {spec}"


def test_line_item_gutters_are_tighter_than_the_rest_of_the_form():
    html = _read()
    assert "#inv-form .form-row { gap: 10px; }" in html, (
        "row gutters not tightened; the Material column cannot claim the space"
    )


def test_material_dropdown_escapes_the_line_item_scroll_box():
    """The line-item list is a capped scroll box (overflow-y:auto), so an
    absolutely positioned dropdown was clipped to a sliver. UI.quickSelect now
    pins the panel to the viewport instead of the column."""
    html = _read()
    assert "#inv-form .quickselect-panel { min-width: 340px; }" in html, (
        "material dropdown is squeezed back to the column width"
    )
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "js", "components.js"), encoding="utf-8") as fh:
        ui = fh.read()
    assert "function placePanel()" in ui, "quickSelect no longer positions its own panel"
    assert 'panel.style.position = "fixed"' in ui, (
        "the dropdown is positioned inside the scroll box again and will be clipped"
    )
    assert ui.count("placePanel();") == 2, (
        "placePanel must run wherever the panel is shown (the match and the "
        "no-match branch), or the list renders unpositioned"
    )
    assert 'panel.style.left = r.left + "px";' in ui, (
        "the panel keeps its inline left:0, so under position:fixed the list "
        "opens at the window edge instead of under the field"
    )

