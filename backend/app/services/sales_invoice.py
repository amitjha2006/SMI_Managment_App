"""Building a sales tax invoice from a delivery challan.

The rules this enforces, in the order they are checked. Each exists because
skipping it produces a tax document that is wrong in a way nobody notices:

1. The challan must be confirmed and dispatched. A draft is not yet a
   receivable so it cannot be billed, and a tax invoice is raised when the
   goods leave, not before. Delivery confirmation is deliberately NOT
   required - the invoice travels with the goods.
2. The challan must be on or after the invoice cut-over. The 89 challans that
   predate it were delivered under a different arrangement and are not
   back-billed.
3. The challan must not already have an invoice. A second invoice for goods
   already billed is double-billing, and it is the one error here that
   directly costs the customer money.
4. Every product on the challan must have a GST rate set. 0 means "not
   configured", and issuing an invoice that silently charged no tax would be
   worse than refusing.
5. The supplier GSTIN must be on file. A tax invoice without it is not a valid
   tax document, and the buyer's accountant will reject it.
6. The extracted tax must reconcile to the challan total. If it does not, the
   invoice is wrong, so it is raised rather than saved.

Amounts are COPIED from the challan and GST is EXTRACTED from them, never
recomputed. That is what makes the invoice total equal the challan total: the
customer is not re-billed for a number that was already agreed and recorded.

Payment is deliberately not touched. Outstanding stays on the challan.
"""

from decimal import Decimal

from backend.app.services import gst as _gst

ZERO = Decimal("0.00")


class InvoiceError(ValueError):
    """A tax invoice cannot be issued. Carries a user-facing reason."""


class InvoiceRefused(InvoiceError):
    """Raised when the caller wants a 4xx (a rule, not a fault).

    Kept distinct from InvoiceError so the endpoint can map "you asked for
    something not allowed" to 400 and "something is broken" to 500, instead of
    reporting every problem as a server error.
    """


def check_invoiceable(challan, invoice_exists, cutover_date):
    """Validate a challan can be invoiced. Returns nothing; raises on refusal.

    Split out from build_invoice so the rules can be tested individually, and
    so the endpoint can call it to produce a useful error before touching the
    database.
    """
    if challan is None:
        raise InvoiceRefused("Challan not found.")

    if getattr(challan, "is_archived", False):
        raise InvoiceRefused(
            "This challan is archived, so it cannot be invoiced. Restore it first."
        )

    raw_status = getattr(challan, "status", "")
    status_val = str(getattr(raw_status, "value", raw_status) or "").lower()
    if status_val == "draft":
        raise InvoiceRefused(
            "This challan is still a draft - confirm it first. A draft is "
            "not yet a receivable, so it cannot be billed."
        )
    if not getattr(challan, "dispatched_at", None):
        raise InvoiceRefused(
            "This challan has not been dispatched yet. A tax invoice is "
            "raised when the goods leave, not before."
        )

    challan_date = getattr(challan, "challan_date", None)
    if cutover_date is not None and challan_date is not None:
        d = challan_date.date() if hasattr(challan_date, "date") else challan_date
        if d < cutover_date:
            raise InvoiceRefused(
                f"Challan {getattr(challan, 'challan_number', '')} is dated {d}, "
                f"before the sales-invoice start date of {cutover_date}. Only "
                f"challans from {cutover_date} onwards can be turned into tax "
                f"invoices - earlier ones stay as delivery notes."
            )

    if invoice_exists:
        raise InvoiceRefused(
            "This challan already has a sales invoice. One challan can only be "
            "invoiced once; create a new challan for any further billing."
        )


def check_products_have_gst_rate(lines, product_by_id):
    """Refuse if any product on the challan has no GST rate configured.

    Returns a list of human-readable warnings rather than raising for the soft
    problems (missing HSN), but raises for the hard one (no rate), because a
    tax invoice at an unstated rate is a wrong document.
    """
    missing = []
    for line in lines:
        pid = getattr(line, "product_id", None)
        product = product_by_id(pid)
        if product is None:
            raise InvoiceRefused(f"Product {pid} on the challan no longer exists.")
        if not _gst.is_rate_configured(product.gst_rate):
            missing.append(f"{product.name} (GST rate not set)")

    if missing:
        raise InvoiceRefused(
            "These products on the challan have no GST rate set, so a correct "
            "tax invoice cannot be produced: "
            + ", ".join(missing)
            + ". Set the rate in Inventory → Edit product → GST rate first."
        )

    # Soft: a missing HSN does not stop the document being issued, but the
    # buyer may need it to claim credit, so it is surfaced rather than hidden.
    warnings = []
    for line in lines:
        product = product_by_id(getattr(line, "product_id", None))
        if product is not None and not (product.hsn_code or "").strip():
            warnings.append(
                f"{product.name} has no HSN code on the invoice. "
                "Set it in Inventory → Edit product → HSN code."
            )
    return warnings


def check_company_gstin(company_gstin):
    """A tax invoice without the supplier's GSTIN is not a valid tax document."""
    if not (company_gstin or "").strip():
        raise InvoiceRefused(
            "The company GSTIN is not set, so a valid tax invoice cannot be "
            "issued. Set it in Settings - Company Profile first. (A delivery "
            "challan does not need it, which is why the challan could be "
            "raised before it was configured.)"
        )
    return company_gstin.strip().upper()


def build_invoice(
    challan,
    company_profile,
    _customer,
    product_by_id,
    intra_state: bool,
    invoice_date,
    _invoice_prefix: str = "INV",
):
    """Compute the invoice header totals and per-line GST. Does NOT save.

    Returns (header_dict, line_dicts, warnings). The caller persists, because
    the sequence number must be reserved inside the same transaction as the
    insert, or a failed save would burn a number.
    """
    lines = list(challan.items or [])
    if not lines:
        raise InvoiceRefused("This challan has no line items, so there is nothing to invoice.")

    check_products_have_gst_rate(lines, product_by_id)
    company_gstin = check_company_gstin(company_profile.get("gstin"))

    # GST is extracted from the challan's GST-INCLUSIVE amounts.
    rates = []
    for line in lines:
        product = product_by_id(line.product_id)
        rates.append((line.amount, product.gst_rate))
    result = _gst.extract_lines(rates, intra_state=intra_state)
    by_line = result["lines"]
    totals = result["totals"]

    # The invariant, checked before anything is written.
    challan_total = sum((Decimal(str(line.amount or 0)) for line in lines), ZERO)
    if totals["amount"] != challan_total:
        raise InvoiceError(
            f"Invoice would not foot to the challan: extracted {totals['amount']} "
            f"against a challan total of {challan_total}. Refusing to issue a "
            f"tax invoice that does not match the delivery document."
        )

    hsn_missing = [
        product_by_id(line.product_id).name
        for line in lines
        if not (product_by_id(line.product_id).hsn_code or "").strip()
    ]
    warnings = [f"{n} has no HSN code on the invoice." for n in hsn_missing]

    header = {
        "invoice_date": invoice_date,
        "customer_id": challan.customer_id,
        "intra_state": bool(intra_state),
        "total_quantity": sum((Decimal(str(line.quantity or 0)) for line in lines), ZERO),
        "total_weight": sum((Decimal(str(line.weight or 0)) for line in lines), ZERO),
        "taxable_value": totals["taxable_value"],
        "total_tax": totals["total_tax"],
        "cgst": totals["cgst"],
        "sgst": totals["sgst"],
        "igst": totals["igst"],
        "grand_total": totals["amount"],
        "company_gstin": company_gstin,
    }

    item_rows = []
    for i, (line, g) in enumerate(zip(lines, by_line, strict=True), start=1):
        product = product_by_id(line.product_id)
        item_rows.append(
            {
                "sequence": i,
                "product_id": line.product_id,
                "packaging_size_id": line.packaging_size_id,
                "product_name": line.product_name or product.name,
                "size_name": line.size_name,
                "hsn_code": (product.hsn_code or "").strip() or None,
                "quantity": Decimal(str(line.quantity or 0)),
                "weight": Decimal(str(line.weight or 0)),
                "rate_per_kg": Decimal(str(line.rate_per_kg or 0)),
                "amount": g["amount"],
                "gst_rate": g["rate"],
                "taxable_value": g["taxable_value"],
                "cgst": g["cgst"],
                "sgst": g["sgst"],
                "igst": g["igst"],
            }
        )

    return header, item_rows, warnings


def resolve_intra_state(setting_value, _supplier_gstin, _customer_gstin):
    """Decide intra- vs inter-state from the company setting.

    Deliberately does NOT try to infer it from the two GSTINs' state codes.
    The customer GST numbers in this database are 10 digits, not the 15 a
    GSTIN requires, so their first two digits cannot be trusted as a state
    code - and billing the wrong head of tax is a real compliance error. So the
    answer comes from an explicit setting the business controls, and this
    function only exists to make that single decision in one place.
    """
    return str(setting_value or "intra").strip().lower() != "inter"
