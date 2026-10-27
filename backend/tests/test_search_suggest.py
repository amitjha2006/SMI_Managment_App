"""Searchable dropdown (UI.searchSuggest) wired into every table search box.

The user asked that every search/find field in the app get the same
click-empty-box-to-see-everything dropdown the form pickers already have
(UI.quickSelect). The helper lives in components.js; each page binds its
own input with a provider that reads the page's live data array.

Run: python -m pytest backend/tests/test_search_suggest.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _read(relpath):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, *relpath.split("/")), encoding="utf-8") as fh:
        return fh.read()


def test_helper_exported_and_dark_safe():
    js = _read("frontend/js/components.js")
    assert "function searchSuggest(inputEl, provider, opts)" in js, "helper missing"
    # Exported on window.UI together with quickSelect.
    assert re_export(js, "searchSuggest"), "searchSuggest not exported from UI"
    # Panel colors go through CSS variables so one rule serves both themes.
    assert "background:var(--color-surface" in js, "panel hardcodes a light color"
    assert "--color-surface-alt" in js, "panel hover lacks the themed hover var"
    i = js.find("function searchSuggest")
    seg = js[i:i + 3000]
    # Hug the text (width:max-content) and anchor to the input itself — the
    # user complained the panel stretched across the whole header row on the
    # Master Data pages, where the search box sits in a full-width flex row.
    assert "width:max-content" in seg, "panel must hug its text, not fill the row"
    assert "inputEl.offsetLeft" in seg and "inputEl.offsetTop" in seg, \
        "panel must be measured from the input, not the whole header row"


def re_export(js, name):
    return name in js[js.find("return { notify"):]


# field id -> page that must bind it
BINDINGS = {
    "cust-search": "frontend/pages/customers.html",
    "sup-search": "frontend/pages/suppliers.html",
    "emp-search": "frontend/pages/employees.html",
    "raw-search": "frontend/pages/raw-materials.html",
    "prod-search": "frontend/pages/inventory.html",
    "challan-search": "frontend/pages/challans.html",
    "sales-search": "frontend/pages/invoices.html",
    "inv-search": "frontend/pages/invoices.html",
    "exp-search": "frontend/pages/expenses.html",
    "pe-search": "frontend/pages/production.html",
    "followup-search": "frontend/pages/followup.html",
    "prsum-search": "frontend/pages/payment-summary.html",
    "ph-search": "frontend/pages/production-history.html",
    "fg-balance-search": "frontend/pages/dashboard.html",
    "help-search": "frontend/pages/help.html",
}


def test_every_search_field_gets_the_dropdown():
    for field, page in BINDINGS.items():
        src = _read(page)
        assert f'id="{field.strip()}"' in src, f"{page} lost its {field} input"
        assert "searchSuggest" in src, f"{page} does not call UI.searchSuggest"


def test_production_search_bound():
    src = _read("frontend/pages/production.html")
    i = src.find("searchSuggest")
    assert i >= 0, "production.html does not bind searchSuggest"
    chunk = src[i:i + 400]
    assert 'pe-search' in chunk, "production searchSuggest is not bound to pe-search"
    # numbers AND product names both searchable on this page
    assert "production_number" in chunk and "product_name" in chunk


def test_dropdown_lists_mirror_every_searchable_field():
    """Each dropdown must offer values for every field its text search
    matches — the user found challan/purchase search filtering on customers
    and suppliers while the dropdown only listed numbers."""
    def provider_chunk(page, field):
        src = _read(page)
        i = src.find('document.getElementById("' + field + '")')
        assert i >= 0, f"{page} lost its {field} binding"
        return src[i:i + 900]

    ch = provider_chunk("frontend/pages/challans.html", "challan-search")
    assert "challan_number" in ch and "customer_name" in ch and "status" in ch, \
        "challan dropdown must list numbers, customers and statuses"
    pur = provider_chunk("frontend/pages/invoices.html", "inv-search")
    assert all(f in pur for f in
               ("invoice_number", "supplier_name", "supplier_company", "payment_status")), \
        "purchase dropdown must mirror number/supplier/company/status search"
    sal = provider_chunk("frontend/pages/invoices.html", "sales-search")
    assert all(f in sal for f in ("invoice_number", "customer_name", "challan_number")), \
        "sales dropdown must mirror number/customer/challan search"
    cust = provider_chunk("frontend/pages/customers.html", "cust-search")
    assert "company_name" in cust and "contact_number" in cust, \
        "customer dropdown must offer companies and phone numbers too"
    sup = provider_chunk("frontend/pages/suppliers.html", "sup-search")
    assert "company_name" in sup and "contact_number" in sup, \
        "supplier dropdown must offer companies and phone numbers too"
    exp = provider_chunk("frontend/pages/expenses.html", "exp-search")
    assert "category_name" in exp and "payment_mode" in exp, \
        "expense dropdown must offer categories and modes too"


def test_analysis_dropdown_replaces_datalist():
    """WebView2 never opens a native datalist on an empty click — exactly the
    complaint — so the Analysis product search must use the shared panel."""
    an = _read("frontend/pages/analysis.html")
    assert 'list="product-suggestions"' not in an, "an-search still on datalist"
    assert "<datalist" not in an, "datalist element still present"
    assert "populateProductSuggestions" in an, "suggestion wiring lost"
    seg = an[an.find("populateProductSuggestions"):]
    assert "searchSuggest" in seg, "populateProductSuggestions no longer binds the panel"


def test_monthly_sales_keeps_its_existing_dropdown():
    """monthly-sales already ships a working suggestion panel; this batch
    deliberately leaves it alone (no double dropdowns)."""
    ms = _read("frontend/pages/monthly-sales.html")
    assert "ms-suggest" in ms, "monthly-sales suggestion panel vanished"


def test_search_suggest_picks_dispatch_input_event():
    """Picking a suggestion must re-run the page's own inline oninput filter
    (dispatched "input" event), plus fire the optional onChange hook."""
    js = _read("frontend/js/components.js")
    i = js.find("function searchSuggest")
    seg = js[i:i + 3000]
    assert 'dispatchEvent(new Event("input"' in seg, "pick does not re-filter"
    assert "onChange" in seg, "optional onChange hook missing"
