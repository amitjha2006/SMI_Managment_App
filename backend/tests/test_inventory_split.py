"""Inventory split: raw materials get their own page.

The combined inventory page is divided in two - raw-material stock on
#/raw-materials (new file), products and packaging on #/products (the
trimmed inventory.html). Each page carries its own adjustment form fixed
to its kind, so a raw adjustment can never land on finished stock. The
old #/inventory route redirects (kind=new=1 aware) so saved links keep
working.

Run: python -m pytest backend/tests/test_inventory_split.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _read(relpath):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, *relpath.split("/")), encoding="utf-8") as fh:
        return fh.read()


def test_raw_materials_route_exists():
    app_js = _read("frontend/js/app.js")
    assert "/raw-materials" in app_js, "no /raw-materials route"
    assert "raw-materials.html" in app_js, "route does not load raw-materials.html"


def test_old_inventory_route_redirects_by_kind():
    app_js = _read("frontend/js/app.js")
    i = app_js.find("old combined Inventory page is split")
    assert i >= 0, "no #/inventory redirect block"
    block = app_js[i:i + 900]
    assert "raw-materials" in block, "#/inventory does not redirect to raw materials"
    assert "kind" in block, "redirect ignores the kind=raw|product deep links"


def test_nav_lists_raw_materials_in_operations():
    index = _read("frontend/index.html")
    assert 'href="#/raw-materials"' in index, "no Raw Materials nav link"
    assert 'href="#/inventory"' not in index, "dead Inventory nav link remains"


def test_raw_materials_page_has_raw_section_and_raw_only_adjustment():
    html = _read("frontend/pages/raw-materials.html")
    for token in ["raw-form", "raw-table", "adj-form", "adj-table", "saveAdjustment"]:
        assert token in html, f"raw-materials page misses {token}"
    assert 'value="finished_product"' not in html, "raw page still offers finished goods"
    assert "raw_material" in html
    for token in ["prod-form", "se-formulation", "prod-table"]:
        assert token not in html, f"products markup leaked into raw page: {token}"


def test_products_page_has_products_and_finished_only_adjustment():
    html = _read("frontend/pages/inventory.html")
    for token in ["prod-form", "prod-table", "adj-form", "adj-table", "saveAdjustment"]:
        assert token in html, f"products page misses {token}"
    assert 'value="raw_material"' not in html, "products page still offers raw materials"
    assert "finished_product" in html
    for token in ["raw-form", "raw-table", "raw-search"]:
        assert token not in html, f"raw markup remains on products page: {token}"


def test_products_page_opens_form_for_new_deep_link():
    html = _read("frontend/pages/inventory.html")
    assert "new=1" in html, "?new=1 (Add Product) opens nothing"
    assert "openNewProductForm" in html


def test_dashboard_links_point_at_the_split_pages():
    html = _read("frontend/pages/dashboard.html")
    assert 'href="#/raw-materials"' in html, "raw card still points at #/inventory"
    assert 'href="#/products?new=1"' in html, "Add Product still points at #/inventory"
    assert 'href="#/inventory?new=1"' not in html, "stale combined-page link remains"
