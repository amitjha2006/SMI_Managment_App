"""Products list category filter: picking a category must filter the table.

Regression: categoryOptions() installed its new-category onchange toggle on
every select it built - including the list filter - clobbering the filter's
inline onchange. Picking a category then silently did nothing while the
dropdown itself looked perfectly fine.

Run: python -m pytest backend/tests/test_product_category_filter.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _page():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "inventory.html"), encoding="utf-8") as fh:
        return fh.read()


def test_filter_keeps_its_onchange():
    html = _page()
    assert 'id="prod-cat-filter" onchange="filterProducts()"' in html
    start = html.find("function categoryOptions")
    assert start >= 0
    # The function ends at the next top-level function; the handler install
    # must sit inside an includeNew guard within it.
    chunk = html[start:start + 2500]
    i = chunk.find("sel.onchange = ")
    assert i >= 0, "no toggle handler installed at all"
    guard = chunk.rfind("if (includeNew)", 0, i)
    assert guard >= 0, "toggle handler is not guarded by includeNew - it clobbers the filter"


def test_filter_compares_category_ids(db):
    db.add(models.ProductCategory(name="Dry"))
    db.add(models.ProductCategory(name="Mix"))
    db.commit()
    dry = db.query(models.ProductCategory).filter_by(name="Dry").one()
    mix = db.query(models.ProductCategory).filter_by(name="Mix").one()
    db.add(models.Product(product_id="P-F1", name="Dry One", category_id=dry.id))
    db.add(models.Product(product_id="P-F2", name="Mix One", category_id=mix.id))
    db.commit()
    rows = m.list_products(db)
    by_id = {p.category_id for p in rows}
    assert by_id == {dry.id, mix.id}, by_id
    assert [p for p in rows if p.category_id == dry.id][0].name == "Dry One"


def test_category_actions_can_reload_products():
    # Regression: add/rename/toggle/delete category called
    # window.loadProducts(), but loadProducts was a local function -
    # activating a category threw and the list never refreshed.
    html = _page()
    assert "window.loadProducts = loadProducts" in html


def test_analysis_category_dropdown_uses_master():
    # Regression: the Analysis category dropdown was built only from
    # categories with non-zero data in range, so a newly created
    # category could never appear in it.
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "analysis.html"), encoding="utf-8") as fh:
        html = fh.read()
    i = html.find("async function loadAnalysis")
    assert i >= 0
    body = html[i:i + 2500]
    assert "/product-categories" in body, "dropdown not fed from the category master"
    assert "activeCats" not in body, "data-only category gate still present"
