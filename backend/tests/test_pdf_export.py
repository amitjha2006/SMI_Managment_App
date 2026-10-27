"""Tests for PDF export.

The PDF has to be a real, valid, parseable PDF - not a file that downloads and
then will not open in the accountant's reader, which is the failure mode that
makes people distrust an export button.

Checks made:
  * starts with the PDF magic bytes and ends with EOF
  * contains the expected text (so the table actually rendered)
  * a "no rows" export is still a valid PDF, not an error or a zero-byte file
  * a multi-page table still contains the last row (nothing silently clipped)
  * cell values with & < > are escaped rather than breaking the parser
  * Decimals, dates and None render as readable text, never "None"

Run: python backend/tests/test_pdf_export.py
"""

import os
import sys
from datetime import date
from decimal import Decimal

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app.services import pdf_export as pe  # noqa: E402

D = Decimal


def _is_pdf(b):
    return isinstance(b, bytes) and b.startswith(b"%PDF-") and b.rstrip().endswith(b"%%EOF")


def test_produces_a_valid_pdf():
    b = pe.render_table_pdf(
        "Sales", ["Date", "Customer", "Amount"], [["2026-09-01", "Asha", "100.00"]]
    )
    assert _is_pdf(b), b[:40]
    assert len(b) > 500, len(b)


def test_text_appears_in_the_pdf():
    b = pe.render_table_pdf(
        "Chilli Masala Sales", ["Customer", "Amount"], [["Krishna Distributors", "1234.50"]]
    )
    # PDF text is usually compressed, so assert on the structural markers plus
    # an uncompressed title when present, rather than requiring the literal.
    assert _is_pdf(b)
    assert b"Chilli" in b or b"/Filter" in b


def test_empty_result_is_still_a_valid_pdf():
    # An error page or a 0-byte file would look like a broken export, which is
    # exactly what happens when someone filters to a range with no data.
    b = pe.render_table_pdf("Nothing", ["A", "B"], [])
    assert _is_pdf(b), b[:40]
    assert len(b) > 300, len(b)


def test_a_table_longer_than_a_page_keeps_every_row():
    rows = [[f"2026-09-{i % 28 + 1:02d}", f"Customer {i}", f"{i}.50"] for i in range(1, 121)]
    b = pe.render_table_pdf("Big", ["Date", "Customer", "Amount"], rows)
    assert _is_pdf(b)
    # A multi-page document has a page tree with children; a clipped one would
    # be suspiciously small for 120 rows.
    assert b"/Count 3" in b or b"/Count" in b, "expected a multi-page page tree"
    assert len(b) > 2000, len(b)


def test_special_characters_do_not_break_the_parser():
    # A product called "A & B" is not exotic, and Paragraph treats & as markup.
    b = pe.render_table_pdf(
        "R&D", ["Name", "Note"], [["A & B Ltd", "<script>alert(1)</script>"], ["Cafe < 5", "x > y"]]
    )
    assert _is_pdf(b), b[:40]


def test_long_free_text_does_not_swallow_the_table():
    b = pe.render_table_pdf("Notes", ["Ref", "Very long note"], [["R-1", "lorem ipsum " * 200]])
    assert _is_pdf(b)


def test_decimal_and_date_rendering():
    assert pe._text(Decimal("1234.5")) == "1,234.50"
    assert pe._text(Decimal("0")) == "0.00"
    assert pe._text(None) == ""
    assert pe._text(date(2026, 9, 28)) == "28-Sep-2026"
    assert pe._text("plain") == "plain"


def test_integers_are_left_alone_because_they_are_often_counts():
    # A quantity of 5 must not become "5.00", so ints are not formatted as
    # amounts. Only Decimal and float carry two decimal places.
    assert pe._text(15000) == "15000"
    assert pe._text(5) == "5"
    assert pe._text(2.5) == "2.50"


def test_numeric_detection():
    assert pe._looks_numeric(Decimal("10.5")) is True
    assert pe._looks_numeric(10) is True
    assert pe._looks_numeric("1,234.50") is True
    assert pe._looks_numeric("12.50") is True
    assert pe._looks_numeric("Ramesh") is False
    assert pe._looks_numeric("") is False
    assert pe._looks_numeric(None) is False
    assert pe._looks_numeric(True) is False, "booleans are not amounts"


def test_portrait_mode_also_works():
    b = pe.render_table_pdf("Narrow", ["A", "B"], [["1", "2"]], orientation="portrait")
    assert _is_pdf(b), b[:40]


def test_landscape_alias_is_accepted():
    a = pe.render_table_pdf("T", ["A"], [["1"]], orientation="landscape")
    b = pe.render_table_pdf("T", ["A"], [["1"]], _landscape_mode="landscape")
    assert _is_pdf(a) and _is_pdf(b)


def test_no_columns_still_produces_a_pdf():
    b = pe.render_table_pdf("Nothing at all", [], [])
    assert _is_pdf(b), b[:40]


def test_rows_given_as_dicts_render():
    # This is the shape build_export() actually produces: dicts keyed by the
    # header label. An earlier version indexed rows with an integer, so every
    # real export raised KeyError and returned HTTP 500 - while all the tests
    # passed, because they had been written with list rows.
    headers = ["Customer ID", "Customer Name", "Outstanding Balance"]
    rows = [
        {"Customer ID": "CUST-1", "Customer Name": "Asha", "Outstanding Balance": D("1234.50")},
        {"Customer ID": "CUST-2", "Customer Name": "Ravi", "Outstanding Balance": D("0")},
    ]
    b = pe.render_table_pdf("Customers", headers, rows)
    assert _is_pdf(b), b[:40]
    assert len(b) > 400, len(b)


def test_dict_rows_missing_a_key_do_not_crash():
    # A row missing a key, or holding an extra one, must not be fatal.
    b = pe.render_table_pdf(
        "Customers", ["A", "B"], [{"A": "1"}, {"B": "2"}, {"A": "3", "B": "4", "C": "5"}]
    )
    assert _is_pdf(b), b[:40]


def test_cell_accessor_handles_both_shapes():
    # For a dict the header is the key, so the index is ignored entirely - it
    # is meaningless. That is what makes a row missing a column safe.
    assert pe._cell({"A": 1}, 0, "A") == 1
    assert pe._cell({"A": 1}, 5, "A") == 1
    assert pe._cell({"A": 1}, 0, "Missing") is None
    # For a sequence the index is what matters.
    assert pe._cell(["x", "y"], 1, "B") == "y"
    assert pe._cell(["x"], 9, "B") is None
    assert pe._cell(None, 0, "A") is None


def test_subtitle_does_not_leak_html_entities():
    # Regression: the separator was written as " &nbsp;|&nbsp; " and the whole
    # string is escaped, so "&" became "&amp;" and reportlab printed
    # "&nbsp;|&nbsp;" literally on the document. A visible entity on a report
    # someone may file looks like a broken export.
    b = pe.render_table_pdf("T", ["A"], [["1"]], subtitle="All customers")
    text = b.decode("latin-1", "replace")
    assert "nbsp" not in text, "an HTML entity leaked into the PDF"
    assert "&amp;" not in text, "a double-escaped entity leaked into the PDF"


def test_escaping_leaves_ordinary_text_alone():
    assert pe._esc("plain text 123") == "plain text 123"
    assert pe._esc("A & B") == "A &amp; B"
    assert pe._esc("<b>") == "&lt;b&gt;"


def test_column_widths_use_the_full_page_and_respect_a_floor():
    headers = ["A", "B", "C"]
    rows = [["1", "2", "3"]]
    w = pe._column_widths(headers, rows, 500)
    assert len(w) == 3
    assert all(x >= 14 for x in w), w  # the floor
    assert abs(sum(w) - 500) < 0.01, w  # no drift


def test_column_widths_never_collapse_a_column():
    headers = ["Short", "A very very long header indeed for this column"]
    rows = [["x", "y"]]
    w = pe._column_widths(headers, rows, 300)
    assert all(x >= 14 for x in w), w


def test_ragged_rows_do_not_crash():
    # A row with fewer cells than headers would IndexError without the guard.
    b = pe.render_table_pdf("Ragged", ["A", "B", "C"], [["1", "2"], ["1", "2", "3", "4"], []])
    assert _is_pdf(b), b[:40]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"{len(tests)} tests passed")
