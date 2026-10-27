"""Pouch weight entry in grams, pre-filled but editable.

Adding a pack size shows a pouch-weight box already filled from the size
(7g->2, 50g->4, 100g->6, 175g->6, 200g->8, 250g->8, 400g->10, 500g->10,
Loose Powder->14 grams); the per-size edit column reads grams too. Grams in,
grams out - kilograms stay a storage detail (divide once on save).

These assertions read the actual mapping table and the event wiring, not just
the presence of a word: the previous version of this file passed while nothing
auto-filled at all, which is how the gap survived.

Run: python -m pytest backend/tests/test_pouch_weight_ui.py -v
"""

import os
import re


def _read():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "inventory.html"), encoding="utf-8") as fh:
        return fh.read()


def _pouch_map():
    """The grams table, parsed so a wrong number fails instead of a substring."""
    html = _read()
    m = re.search(r"POUCH_G_BY_SIZE\s*=\s*\{([^}]*)\}", html)
    assert m, "no POUCH_G_BY_SIZE table on the products page"
    pairs = dict(re.findall(r"(\d+)\s*:\s*(\d+)", m.group(1)))
    return {int(k): int(v) for k, v in pairs.items()}


def test_every_size_has_the_agreed_pouch_weight():
    assert _pouch_map() == {7: 2, 50: 4, 100: 6, 175: 6, 200: 8, 250: 8, 400: 10, 500: 10}


def test_loose_powder_has_its_own_bag_weight():
    html = _read()
    assert re.search(r"LOOSE_POUCH_G\s*=\s*14\b", html), (
        "loose powder no longer resolves to 14g"
    )
    assert 'indexOf("loose")' in html, (
        "loose powder must be matched by name - it shares the 1kg weight"
    )


def test_typing_a_size_fills_the_pouch_box():
    html = _read()
    for box in ('id="as-size"', 'id="as-weight"'):
        i = html.find(box)
        assert i >= 0, f"{box} is gone"
        line = html[i:i + 400]
        assert "syncPouchFromSize()" in line, f"{box} does not refresh the pouch box"
    assert 'id="as-pouch"' in html, "no pouch box on the add-size form"


def test_pouch_box_gives_way_to_a_typed_value():
    html = _read()
    i = html.find('id="as-pouch"')
    assert i >= 0
    line = html[i:i + 500]
    assert 'data-auto="1"' in line, "auto-fill is not armed on the pouch box"
    assert "this.dataset.auto='0'" in line, (
        "a typed pouch weight does not stop the auto-fill overwriting it"
    )


def test_new_product_form_has_the_same_pair():
    html = _read()
    assert 'id="pack-pouch"' in html, "the new-product form has no pouch box"
    assert 'id="pack-wrapper"' in html, "the new-product form has no wrapper roll picker"
    assert "syncProductPouch()" in html, "new-product size/weight does not fill the pouch box"


def test_blank_pouch_lets_the_server_apply_the_default():
    """Blank means "use the size default"; 0 means "no wrapper on purpose".
    Sending an explicit 0 for a blank box would disable the default."""
    html = _read()
    i = html.find("window.addSizeToProduct")
    assert i >= 0
    body = html[i:i + 2500]
    assert "pouchTyped" in body, "blank and zero are not distinguished on add-size"
    assert "if (pouchTyped) sizePayload.wrapper_weight_kg" in body


def test_edit_column_reads_grams():
    html = _read()
    assert "Pouch wt (g)" in html, "edit column still labels kilograms"
    i = html.find("window.saveSize")
    assert i >= 0
    assert "/ 1000" in html[i:i + 1500], (
        "edit save stores grams as kilograms without converting"
    )
