"""Purchase invoice rows show the material's unit beside Qty.

The material picker knows each unit but the Qty box never shows it, so a
quantity is entered blind (kg? bag? litre?). The unit must ride along on
the option and render next to Qty, refreshed on every material change.

Run: python -m pytest backend/tests/test_invoice_unit_hint.py -v
"""

import os


def _read():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "invoices.html"), encoding="utf-8") as fh:
        return fh.read()


def test_material_options_carry_the_unit():
    html = _read()
    assert "data-unit" in html, "material options do not carry the unit"


def test_qty_shows_the_unit_and_refreshes_on_change():
    html = _read()
    assert "inv-unit" in html, "no unit readout beside Qty"
    i = html.find("matSel.addEventListener")
    assert i >= 0
    assert "inv-unit" in html[i:i + 600], (
        "unit readout is not refreshed on material change"
    )
