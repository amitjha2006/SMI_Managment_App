"""Tests for building a sales tax invoice from a delivery challan.

The rules exist to stop four specific silent failures:

  * back-billing the 89 challans that predate the invoice cut-over
  * double-billing one challan with two invoices
  * issuing an invoice that charges no GST because nobody set a rate
  * issuing an invoice whose total silently differs from the challan

The last one is the arithmetic core, and it is asserted end to end rather than
per function, because that is the property the buyer's accountant checks.

Run: python backend/tests/test_sales_invoice.py
"""

import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app.services import sales_invoice as si  # noqa: E402

D = Decimal
CUT = date(2026, 9, 28)


class P:
    """Stand-in product."""

    def __init__(self, pid, name, gst_rate=5, hsn_code="09109100"):
        self.id = pid
        self.name = name
        self.gst_rate = D(str(gst_rate))
        self.hsn_code = hsn_code


class L:
    """Stand-in challan line."""

    def __init__(
        self, product_id, amount, quantity=1, weight=0.1, rate=100, name="Coriander", size="100g"
    ):
        self.product_id = product_id
        self.amount = D(str(amount))
        self.quantity = D(str(quantity))
        self.weight = D(str(weight))
        self.rate_per_kg = D(str(rate))
        self.product_name = name
        self.size_name = size
        self.packaging_size_id = 1


class CH:
    """Stand-in challan."""

    def __init__(self, items, number="CH/26-27/0001", when=None, archived=False,
                 status="confirmed", dispatched=None):
        self.items = items
        self.challan_number = number
        self.challan_date = when or datetime(2026, 9, 28, tzinfo=timezone.utc)
        self.is_archived = archived
        self.customer_id = 1
        self.company_gstin = "10BQYPJ6507J1ZK"
        self.status = status
        self.dispatched_at = dispatched or datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc)


def _catalog(*products):
    table = {p.id: p for p in products}
    return table.get


# --- rule 1: the cut-over boundary ------------------------------------------


def test_a_challan_from_today_can_be_invoiced():
    ch = CH([L(1, 1000)])
    si.check_invoiceable(ch, invoice_exists=False, cutover_date=CUT)  # no raise


def test_a_challan_from_before_the_cutdown_is_refused():
    # This is the rule that protects the 89 existing challans.
    ch = CH([L(1, 1000)], number="CH-45181", when=datetime(2026, 8, 20, tzinfo=timezone.utc))
    try:
        si.check_invoiceable(ch, invoice_exists=False, cutover_date=CUT)
    except si.InvoiceRefused as e:
        assert "before the sales-invoice start date" in str(e)
        assert "CH-45181" in str(e)
        return
    raise AssertionError("a pre-cutover challan should have been refused")


def test_the_cutdown_day_itself_is_invoiceable():
    ch = CH([L(1, 1000)], when=datetime(2026, 9, 28, 0, 0, tzinfo=timezone.utc))
    si.check_invoiceable(ch, invoice_exists=False, cutover_date=CUT)


def test_one_second_before_the_cutdown_is_refused():
    ch = CH([L(1, 1000)], when=datetime(2026, 9, 27, 23, 59, 59, tzinfo=timezone.utc))
    try:
        si.check_invoiceable(ch, invoice_exists=False, cutover_date=CUT)
    except si.InvoiceRefused:
        return
    raise AssertionError("a challan one second early should have been refused")


# --- rule 2: one invoice per challan ----------------------------------------


def test_a_second_invoice_is_refused():
    ch = CH([L(1, 1000)])
    try:
        si.check_invoiceable(ch, invoice_exists=True, cutover_date=CUT)
    except si.InvoiceRefused as e:
        assert "already has a sales invoice" in str(e)
        return
    raise AssertionError("double-billing should have been refused")


def test_an_archived_challan_is_refused():
    ch = CH([L(1, 1000)], archived=True)
    try:
        si.check_invoiceable(ch, invoice_exists=False, cutover_date=CUT)
    except si.InvoiceRefused as e:
        assert "archived" in str(e)
        return
    raise AssertionError("an archived challan should have been refused")


# --- rule 1b: confirmed + dispatched before billing ---------------------------


def test_a_draft_challan_cannot_be_invoiced():
    ch = CH([L(1, 1000)], status="draft")
    try:
        si.check_invoiceable(ch, invoice_exists=False, cutover_date=CUT)
    except si.InvoiceRefused as e:
        assert "draft" in str(e)
        return
    raise AssertionError("a draft challan should have been refused")


def test_an_undispatched_challan_cannot_be_invoiced():
    ch = CH([L(1, 1000)])
    ch.dispatched_at = None
    try:
        si.check_invoiceable(ch, invoice_exists=False, cutover_date=CUT)
    except si.InvoiceRefused as e:
        assert "dispatched" in str(e)
        return
    raise AssertionError("an undispatched challan should have been refused")


# --- rule 3: GST rate must be configured ------------------------------------


def test_a_product_with_no_gst_rate_blocks_the_invoice():
    ch = CH([L(1, 1000)])
    try:
        si.check_products_have_gst_rate(ch.items, _catalog(P(1, "Coriander", gst_rate=0)))
    except si.InvoiceRefused as e:
        assert "no GST rate set" in str(e)
        assert "Coriander" in str(e)
        return
    raise AssertionError("an unconfigured GST rate should have blocked the invoice")


def test_every_product_on_the_challan_must_have_a_rate():
    # One unset product is enough to block the whole invoice - a partial
    # invoice would be a wrong document.
    ch = CH([L(1, 1000), L(2, 500, name="Turmeric")])
    try:
        si.check_products_have_gst_rate(
            ch.items, _catalog(P(1, "Coriander", gst_rate=5), P(2, "Turmeric", gst_rate=0))
        )
    except si.InvoiceRefused as e:
        assert "Turmeric" in str(e)
        return
    raise AssertionError("should have blocked on the second product")


def test_a_missing_product_blocks_invoicing():
    ch = CH([L(99, 1000)])
    try:
        si.check_products_have_gst_rate(ch.items, _catalog(P(1, "Coriander")))
    except si.InvoiceRefused as e:
        assert "no longer exists" in str(e)
        return
    raise AssertionError("a deleted product should have blocked invoicing")


# --- rule 4: the supplier GSTIN is mandatory on a tax invoice ---------------


def test_no_company_gstin_blocks_the_invoice():
    try:
        si.check_company_gstin("")
    except si.InvoiceRefused as e:
        assert "GSTIN" in str(e)
        return
    raise AssertionError("a missing company GSTIN should have blocked the invoice")


def test_company_gstin_is_upper_cased():
    assert si.check_company_gstin(" 10bqypj6507j1zk ") == "10BQYPJ6507J1ZK"


# --- the arithmetic: the invoice must foot to the challan -------------------


def test_invoice_total_equals_challan_total():
    ch = CH([L(1, 1150), L(2, 1050, name="Turmeric"), L(3, 999.99, name="Chilli")])
    prof = {"gstin": "10BQYPJ6507J1ZK"}
    header, items, _ = si.build_invoice(
        ch,
        prof,
        None,
        _catalog(P(1, "Coriander", 15), P(2, "Turmeric", 5), P(3, "Chilli", 18)),
        intra_state=True,
        invoice_date=ch.challan_date,
    )
    challan_total = D("3199.99")
    assert header["grand_total"] == challan_total, header
    assert header["taxable_value"] + header["total_tax"] == challan_total


def test_each_line_reconciles_independently():
    ch = CH([L(1, 1150, name="A"), L(2, 333.33, name="B"), L(3, 7.77, name="C")])
    header, items, _ = si.build_invoice(
        ch,
        {"gstin": "10BQYPJ6507J1ZK"},
        None,
        _catalog(P(1, "A", 15), P(2, "B", 12), P(3, "C", 5)),
        intra_state=True,
        invoice_date=ch.challan_date,
    )
    for it in items:
        assert it["taxable_value"] + it["cgst"] + it["sgst"] + it["igst"] == it["amount"], it


def test_gst_rate_is_frozen_onto_each_line():
    # A rate change on the product afterwards must not reach the issued invoice,
    # which is why the rate is copied rather than referenced.
    ch = CH([L(1, 1000)])
    prod = P(1, "Coriander", 5)
    header, items, _ = si.build_invoice(
        ch,
        {"gstin": "10BQYPJ6507J1ZK"},
        None,
        _catalog(prod),
        intra_state=True,
        invoice_date=ch.challan_date,
    )
    assert items[0]["gst_rate"] == D("5")
    prod.gst_rate = D("28")  # the business changes the rate
    assert items[0]["gst_rate"] == D("5"), "the line kept the issued rate"


def test_intra_state_charges_cgst_and_sgst():
    ch = CH([L(1, 1180)])
    header, items, _ = si.build_invoice(
        ch,
        {"gstin": "10BQYPJ6507J1ZK"},
        None,
        _catalog(P(1, "X", 18)),
        intra_state=True,
        invoice_date=ch.challan_date,
    )
    assert header["cgst"] == D("90.00"), header
    assert header["sgst"] == D("90.00"), header
    assert header["igst"] == D("0.00"), header


def test_inter_state_charges_igst():
    # The factory is in Bihar and a customer outside it is an inter-state
    # supply, which must be IGST. Billing CGST/SGST instead would be wrong and
    # would cost the buyer their input credit.
    ch = CH([L(1, 1180)])
    header, items, _ = si.build_invoice(
        ch,
        {"gstin": "10BQYPJ6507J1ZK"},
        None,
        _catalog(P(1, "X", 18)),
        intra_state=False,
        invoice_date=ch.challan_date,
    )
    assert header["igst"] == D("180.00"), header
    assert header["cgst"] == D("0.00"), header
    assert header["sgst"] == D("0.00"), header
    assert header["taxable_value"] + header["igst"] == D("1180.00")


def test_supply_type_resolution():
    # Deliberately NOT inferred from the GSTINs: the customer numbers in this
    # database are 10 digits, not 15, so their first two digits cannot be
    # trusted as a state code.
    assert si.resolve_intra_state("intra", None, None) is True
    assert si.resolve_intra_state("INTER", None, None) is False
    assert si.resolve_intra_state("", None, None) is True  # defaults intra
    assert si.resolve_intra_state(None, "10ABC", "27XYZ") is True  # setting wins


def test_a_challan_with_no_lines_is_refused():
    ch = CH([])
    try:
        si.build_invoice(
            ch,
            {"gstin": "10BQYPJ6507J1ZK"},
            None,
            _catalog(),
            intra_state=True,
            invoice_date=ch.challan_date,
        )
    except si.InvoiceRefused as e:
        assert "no line items" in str(e)
        return
    raise AssertionError("an empty challan should have been refused")


def test_missing_hsn_is_a_warning_not_a_block():
    # A missing HSN does not make the arithmetic wrong, so the invoice is still
    # issued - but the user is told, rather than left with a silently
    # non-compliant document.
    ch = CH([L(1, 1000)])
    header, items, warnings = si.build_invoice(
        ch,
        {"gstin": "10BQYPJ6507J1ZK"},
        None,
        _catalog(P(1, "Coriander", 5, hsn_code=None)),
        intra_state=True,
        invoice_date=ch.challan_date,
    )
    assert items[0]["hsn_code"] is None
    assert warnings and "HSN" in warnings[0]


def test_refusal_and_fault_are_distinguishable():
    # The endpoint maps these to 400 and 500. If they were the same type, every
    # business rule would be reported to the user as a server error.
    assert issubclass(si.InvoiceRefused, si.InvoiceError)
    assert not issubclass(si.InvoiceError, si.InvoiceRefused)


def test_real_challan_totals_from_the_live_data_invoice_correctly():
    for amt in ["14025.00", "12076.50", "3250.00", "1800.00", "14025"]:
        ch = CH([L(1, amt)])
        header, _, _ = si.build_invoice(
            ch,
            {"gstin": "10BQYPJ6507J1ZK"},
            None,
            _catalog(P(1, "X", 12)),
            intra_state=True,
            invoice_date=ch.challan_date,
        )
        assert header["taxable_value"] + header["total_tax"] == header["grand_total"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"{len(tests)} tests passed")
