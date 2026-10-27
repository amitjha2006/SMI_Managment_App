"""Low-stock deep links: every shown name opens filtered inventory.

The dashboard merges raw-material and finished-pack names today, so a link
cannot target the right table. Names must link per kind:
#/inventory?search=<name>&kind=raw (or product), which redirects to the
split pages, and each page must honor the deep link by prefilling its
matching search box.

Run: python -m pytest backend/tests/test_low_stock_links.py -v
"""

import os


def _read(rel):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


def test_low_stock_names_link_per_kind():
    dash = _read("frontend/pages/dashboard.html")
    assert "&kind=${kind}" in dash, "low-stock links must carry the kind"
    assert 'lowLink(n, "raw")' in dash, "raw-material names must link as raw"
    assert 'lowLink(n, "product")' in dash, "pack names must link as product"
    assert "#/inventory?search=" in dash, "low names must link into inventory search"


def test_inventory_honors_search_deep_link():
    raw = _read("frontend/pages/raw-materials.html")
    assert "search=" in raw, "raw page must read the search deep-link from the URL"
    assert "raw-search" in raw
    assert "filterRaw" in raw
    prod = _read("frontend/pages/inventory.html")
    assert "search=" in prod, "products page must read the search deep-link from the URL"
    assert "prod-search" in prod
    # The prefilled box must drive its table: raw load honors a prefilled
    # box, products filter unconditionally after load.
    assert "filterProducts" in prod
    assert "new=1" in prod, "?new=1 (Add Product) must open the product form"


def test_attention_low_stock_links_use_in_scope_escaping():
    # Regression: loadAttention is a top-level function while esc() lives
    # inside the page IIFE, so calling esc() there threw ReferenceError and
    # blanked the whole Attention box with "esc is not defined".
    import re

    dash = _read("frontend/pages/dashboard.html")
    start = dash.find("async function loadAttention()")
    assert start >= 0
    # To the matching top-level end: the function ends right before the
    # next top-level async function.
    nxt = dash.find("\nasync function ", start + 1)
    body = dash[start:nxt if nxt > 0 else len(dash)]
    code = "\n".join(
        line.split("//", 1)[0] for line in body.splitlines()
    )
    bare = list(re.finditer(r"(?<![\w$])esc\(", code))
    assert not bare, (
        "loadAttention calls out-of-scope esc() - the whole box dies when "
        "low stock renders"
    )
