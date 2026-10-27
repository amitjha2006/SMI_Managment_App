"""Purchase invoice numbers sort by FY then sequence.

Invoice numbers share the FY form (PUR/26-27/0001, legacy PUR-48213, or a
hand-entered supplier number). The shared digit-strip sorter collapses
every FY number to 26 - and parses legacy "PUR-48213" as negative - so
ascending and descending come out identical. The table must carry a
sortValue hook for its number column, like challans (challanNumKey)
and reports (reportInvoiceKey) already do.

Run: python -m pytest backend/tests/test_invoice_number_sort.py -v
"""

import os
import re


def _read():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "invoices.html"), encoding="utf-8") as fh:
        return fh.read()


def test_invoice_number_column_sorts_by_fy_key():
    html = _read()
    assert re.search(r"sortValue\s*:\s*\{[^}]*\bnumber\b\s*:", html), (
        "purchase invoice numbers sort by digit-strip: every FY number ties"
    )
