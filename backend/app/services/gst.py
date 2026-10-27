"""GST extraction for sales invoices.

The contract that makes this correct, and unusual:

    Challan amounts are GST-INCLUSIVE. The invoice EXTRACTS tax from them,
    it never adds tax on top.

So a line of 1150.00 at 15% is not "1150 + 172.50". It is 1000.00 of goods
plus 150.00 of tax, because the customer was quoted 1150 and that is what
the invoice must total. This is the decision the business confirmed, and it
is the opposite of the usual "add GST to the base" flow, so it lives in one
tested place rather than being re-derived at each call site.

Rounding
--------
`extract` derives the taxable value and then computes tax as the REMAINDER:

    taxable = round(amount / (1 + rate), 2)
    tax     = amount - taxable

Computing the tax directly as `amount * rate / (1 + rate)` and rounding it
independently would let the two parts drift by a paisa, so an invoice would
not foot to its challan. Taking the difference makes
`taxable + tax == amount` exact by construction, for every rate, forever.
That identity is the property the tests actually assert.
"""

from decimal import ROUND_HALF_UP, Decimal

TWO = Decimal("0.01")
ZERO = Decimal("0.00")

# Guard rail. Real GST slabs are small; anything at or above this is a typo
# (someone entered 18 meaning 18%, or 0.18 meaning 18%) and would silently
# produce a wildly wrong invoice, so it is refused rather than clamped.
MAX_RATE = Decimal("100")


class GstError(ValueError):
    """A rate that cannot be used to build a tax invoice."""


def _d(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return ZERO
    return Decimal(str(value))


def normalise_rate(rate) -> Decimal:
    """Validate a percentage rate like 5, 5.0, '5.00' or Decimal('5.00').

    Raises GstError for anything that is not a sane percentage. Notably it
    rejects a bare 0, because 0 means "GST rate not set" on a product and
    must not be mistaken for a genuine 0% (nil-rated) exemption.
    """
    r = _d(rate)
    if r < ZERO:
        raise GstError(f"GST rate cannot be negative (got {r}).")
    if r >= MAX_RATE:
        raise GstError(
            f"GST rate {r} is out of range. Enter a percentage such as 5 or 12, "
            f"not a fraction and not 100 or more."
        )
    return r


def is_rate_configured(rate) -> bool:
    """False when the rate is still the unset default of 0.

    Used to block invoicing rather than guess: a product nobody has set a
    rate on must not silently invoice at 0%.
    """
    return _d(rate) > ZERO


def extract(amount, rate, intra_state: bool = True) -> dict:
    """Split a GST-INCLUSIVE line amount into taxable value and tax.

    Returns dict with: amount (as given), rate, taxable_value, total_tax,
    cgst, sgst, igst.

    `intra_state` selects the head of tax:
      True  - supply within one state: CGST + SGST (each half the total)
      False - supply between states: a single IGST line for the whole amount

    This is not a detail. A supplier registered in Bihar (GSTIN starting 10)
    selling to a buyer in Maharashtra (27) makes an INTER-state supply, which
    is charged as IGST - billing CGST/SGST instead would be wrong and would
    leave the buyer unable to claim input credit. The factory is in Bihar, so
    the answer depends on where each customer is, which is why it is a
    parameter and a company setting rather than a constant.

    Either way `taxable_value + total_tax == amount` still holds exactly.
    """
    amt = _d(amount).quantize(TWO, rounding=ROUND_HALF_UP)
    r = normalise_rate(rate)

    if amt < ZERO:
        raise GstError(f"Amount cannot be negative (got {amt}).")

    if r == ZERO:
        # Guarded by normalise_rate's siblings in the caller, but a genuine
        # nil-rated line must still be handled without dividing by 1+0
        # producing a division surprise. Taxable == amount, tax == 0.
        taxable, tax = amt, ZERO
    else:
        taxable = (amt / (Decimal(1) + r / Decimal(100))).quantize(TWO, rounding=ROUND_HALF_UP)
        # Remainder, so taxable + tax == amt exactly. Never rounded
        # independently: that is the bug this comment exists to prevent.
        tax = amt - taxable

    cgst = sgst = igst = ZERO
    if tax != ZERO:
        if intra_state:
            half = (tax / Decimal(2)).quantize(TWO, rounding=ROUND_HALF_UP)
            cgst = half
            sgst = tax - cgst  # absorbs any rounding residue
        else:
            igst = tax

    return {
        "amount": amt,
        "rate": r,
        "taxable_value": taxable,
        "total_tax": tax,
        "cgst": cgst,
        "sgst": sgst,
        "igst": igst,
        "intra_state": bool(intra_state),
    }


def extract_lines(lines, intra_state: bool = True) -> dict:
    """Extract GST across several lines and total them up.

    `lines` is an iterable of (amount, rate) pairs - the per-product rate is
    what allows a mixed-rate invoice to split correctly.

    The returned totals are the SUM OF THE LINES, not a re-derivation from the
    grand total. Re-deriving at the total level would be wrong the moment two
    lines carry different rates, and would also lose the odd-paisa
    corrections each line made.
    """
    rows = []
    totals = {
        "taxable_value": ZERO,
        "total_tax": ZERO,
        "cgst": ZERO,
        "sgst": ZERO,
        "igst": ZERO,
        "amount": ZERO,
    }
    for amount, rate in lines:
        r = extract(amount, rate, intra_state)
        rows.append(r)
        for k, v in totals.items():
            totals[k] = v + r[k]
    for k, v in totals.items():
        totals[k] = v.quantize(TWO, rounding=ROUND_HALF_UP)

    # The invariant the whole design exists to satisfy. If this ever fails,
    # an invoice would not foot to its challan and the bug is in extract().
    if totals["taxable_value"] + totals["total_tax"] != totals["amount"]:
        raise GstError(
            "Internal error: GST extraction does not reconcile "
            f"({totals['taxable_value']} + {totals['total_tax']} "
            f"!= {totals['amount']})"
        )
    return {"lines": rows, "totals": totals, "intra_state": bool(intra_state)}


# A GSTIN is 15 characters: 2-digit state code + 10-char PAN + entity/checksum.
GSTIN_LENGTH = 15


def state_code_from_gstin(gstin) -> str:
    """First two digits of a GSTIN - the state/union territory code.

    Returns "" for anything that is not a plausible GSTIN, because guessing a
    state from a malformed number is worse than not knowing. Callers treat ""
    as "cannot infer" and fall back to the explicit company setting.
    """
    s = (gstin or "").strip().upper()
    if len(s) != GSTIN_LENGTH or not s[:2].isdigit():
        return ""
    return s[:2]
