"""P&L loss styling must not paint the whole block red.

When the month runs at a loss, only the Profit row turns red. Painting
the entire block red makes every label and figure light-red, which
vanishes on a black-and-white printout - the section prints "without
data" although every figure loaded fine.

Run: python -m pytest backend/tests/test_profit_loss_style.py -v
"""

import os
import re


def _read():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, *(["frontend", "pages", "monthly-sales.html"])),
              encoding="utf-8") as fh:
        return fh.read()


def test_loss_marks_only_the_profit_row():
    html = _read()
    i = html.find("window.loadProfit")
    assert i >= 0
    body = html[i:i + 2500]
    assert "el.style.color" not in body, (
        "a loss recolors the whole P&L block, hiding every row in B&W print"
    )


def test_profit_row_still_flags_a_loss():
    html = _read()
    i = html.find("window.loadProfit")
    assert i >= 0
    body = html[i:i + 2500]
    assert "profit < 0" in body and "color" in body, (
        "a loss month no longer flags the Profit row at all"
    )


def test_print_layout_keeps_rows_on_the_page():
    # A wide pivot table stretches the print layout past A4; block rows
    # then fill that width and their right-aligned figures land off-paper
    # and clip out of the PDF. Capping the print width keeps P&L figures
    # on the page.
    html = _read()
    i = html.find("@media print")
    assert i >= 0
    body = html[i:i + 800]
    assert re.search(r"\.main-wrapper\s*\{[^}]*min-width\s*:\s*0", body), (
        "print layout can blow out to content width, pushing figures off-paper"
    )
    assert re.search(r"\.main-content\s*\{[^}]*max-width\s*:\s*100%", body), (
        "print content is uncapped, so rows can render wider than the page"
    )
