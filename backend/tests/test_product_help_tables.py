"""Product Help rich tables (safe HTML subset).

Product Help accepts a small allowlist of HTML table/formatting tags so
recipes can carry real tables. Everything else stays plain text, so a help
note can never become stored XSS.

Run: python -m pytest backend/tests/test_product_help_tables.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from types import SimpleNamespace  # noqa: E402

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _req():
    return SimpleNamespace(query_params={"reason": "test change note"}, headers={}, client=None)


def _product(db):
    prod = models.Product(product_id="P-HT1", name="Table Help Masala")
    db.add(prod)
    db.commit()
    return prod


def test_help_table_markup_round_trips(db):
    p = _product(db)
    html = (
        "<table><tr><th>Spice</th><th>Share</th></tr>"
        "<tr><td>Coriander</td><td>40%</td></tr></table>"
    )
    out = m.set_product_help(p.id, {"help_text": html}, _req(), db)
    assert "<table>" in out["help_text"], out
    assert "<td>Coriander</td>" in out["help_text"], out
    assert m.get_product_help(p.id, db)["help_text"] == out["help_text"]


def test_help_strips_active_content(db):
    p = _product(db)
    out = m.set_product_help(
        p.id,
        {
            "help_text": (
                '<table onclick="evil()"><tr><td>x</td></tr></table>'
                "<script>alert(1)</script>"
                '<img src="x" onerror="evil()">nota'
            )
        },
        _req(),
        db,
    )
    saved = out["help_text"]
    assert "<script" not in saved and "onclick" not in saved, saved
    assert "<img" not in saved and "onerror" not in saved, saved
    assert "nota" in saved, saved
    assert "<table>" in saved and "<td>x</td>" in saved, saved


def test_help_plain_text_verbatim(db):
    p = _product(db)
    text = "Roast slow, grind fine."
    out = m.set_product_help(p.id, {"help_text": text}, _req(), db)
    assert out["help_text"] == text, out


def test_help_table_editor_ui_pins():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "inventory.html"), encoding="utf-8") as fh:
        inv = fh.read()
    assert "ph-insert-table" in inv, "help editor has no insert-table button"


def test_help_excel_button_ui_pins():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "inventory.html"), encoding="utf-8") as fh:
        inv = fh.read()
    for needle in ("ph-excel-table", "tsvToTable"):
        assert needle in inv, f"help editor has no Excel-to-table conversion: {needle}"


def test_help_table_with_whitespace_round_trips(db):
    # The Excel converter emits indented multi-line markup; the sanitizer
    # must keep its structure, not just single-line tables.
    p = _product(db)
    html = (
        "<table>\n<tr><th>Item</th><th>Share</th></tr>\n"
        "<tr><td>Sugar</td><td>5%</td></tr>\n</table>"
    )
    out = m.set_product_help(p.id, {"help_text": html}, _req(), db)
    assert "<table>" in out["help_text"] and "<th>Item</th>" in out["help_text"], out
    assert "<td>Sugar</td>" in out["help_text"], out


def test_help_table_render_ui_pins():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "production.html"), encoding="utf-8") as fh:
        prod = fh.read()
    assert "renderHelpHtml" in prod, "production guidance does not render help HTML"
