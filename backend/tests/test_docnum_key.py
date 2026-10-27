"""One shared FY document-number key for every number column.

Challan, purchase-invoice and report numbers share one shape (FY
CH/26-27/0001, legacy CH-45181, hand-entered supplier text) and one
failure mode under the digit-strip sorter. Three page-local copies of
the same key would drift, so all three pages use the single shared
UI.docNumKey helper.

Run: python -m pytest backend/tests/test_docnum_key.py -v
"""

import os
import re


def _read(rel):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


def test_shared_doc_number_key_exists():
    js = _read("frontend/js/components.js")
    assert re.search(r"function docNumKey\(", js), "no shared UI.docNumKey helper"
    assert "docNumKey" in js.split("return {")[-1], "UI.docNumKey not exported"


def test_all_number_columns_use_the_shared_key():
    for page in (
        "frontend/pages/challans.html",
        "frontend/pages/invoices.html",
        "frontend/pages/reports.html",
    ):
        html = _read(page)
        assert "docNumKey" in html, f"{page} does not use the shared number key"


def test_no_page_keeps_a_local_number_key():
    for page, local in (
        ("frontend/pages/challans.html", "challanNumKey"),
        ("frontend/pages/invoices.html", "invoiceNumKey"),
        ("frontend/pages/reports.html", "reportInvoiceKey"),
    ):
        html = _read(page)
        assert not re.search(rf"function {local}\(", html), (
            f"{page} still defines local {local} instead of the shared key"
        )
