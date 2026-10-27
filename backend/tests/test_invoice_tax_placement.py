"""Invoice tax breakup: CGST/SGST/IGST label sits under the GST % column.

The 12-column invoice table is #, Product, Size, HSN, Qty, Weight, Rate,
Amount, Taxable, GST %, Tax, Total. The breakup label ("CGST @ 2.5%") used
to span columns 1-8, putting the text under #. It belongs under GST %
(0-based index 9). Amounts under Tax/Total are untouched.

Covers both renderers: the print HTML in challans.html and the PDF footer
rows built by pdf_export._tax_foot_rows.

Run: python -m pytest backend/tests/test_invoice_tax_placement.py -v
"""

import re
import sys

ROOT = r"C:\Shresth_Masala_Application"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.app.services import pdf_export as pe  # noqa: E402


def _read(rel):
    with open(ROOT + "\\" + rel.replace("/", "\\"), encoding="utf-8") as fh:
        return fh.read()


def test_print_label_sits_under_gst_column():
    html = _read("frontend/pages/challans.html")
    assert "CGST @ " in html and "SGST @ " in html
    assert '<td colspan="8">CGST' not in html, (
        "CGST label still spans columns 1-8, i.e. sits under #"
    )
    assert '<td colspan="8">SGST' not in html
    assert '<td colspan="8">IGST' not in html
    # Nine cells before the label puts it at 0-based index 9 (GST %).
    assert re.search(r'<td colspan="9"></td><td class="num">CGST', html), (
        "CGST label is not placed under the GST % column"
    )
    assert re.search(r'<td colspan="9"></td><td class="num">SGST', html), (
        "SGST label is not placed under the GST % column"
    )


def _texts(rows):
    return [[p.text for p in r] for r in rows]


def _styles():
    from reportlab.lib.styles import getSampleStyleSheet

    s = getSampleStyleSheet()
    return s["Normal"], s["Normal"]


def test_pdf_label_sits_under_gst_column():
    cell, cellR = _styles()
    items = [{"gst_rate": 5, "cgst": 100.0, "sgst": 100.0, "igst": 0.0}]
    rows = pe._tax_foot_rows({"intra_state": True}, items, cell, cellR)
    assert len(rows) == 2, rows
    for r in rows:
        assert len(r) == 12, f"footer row must span all 12 columns: {len(r)}"
    texts = _texts(rows)
    assert all(t == "" for t in texts[0][:9]), texts[0]
    assert all(t == "" for t in texts[1][:9]), texts[1]
    assert texts[0][9].startswith("CGST @ "), texts[0]
    assert texts[1][9].startswith("SGST @ "), texts[1]
    # Amounts untouched: still under Tax (10) and Total (11).
    assert "100" in texts[0][10] and "100" in texts[0][11], texts[0]


def test_pdf_inter_state_label_sits_under_gst_column():
    cell, cellR = _styles()
    items = [{"gst_rate": 5, "cgst": 0.0, "sgst": 0.0, "igst": 200.0}]
    rows = pe._tax_foot_rows({"intra_state": False}, items, cell, cellR)
    assert len(rows) == 1, rows
    assert len(rows[0]) == 12
    texts = _texts(rows)
    assert all(t == "" for t in texts[0][:9]), texts[0]
    assert texts[0][9].startswith("IGST @ "), texts[0]
