"""Indian numbering system: an amount written out in words.

Standard on Indian business documents - a printed challan or invoice that shows
"Rs 1,23,456.00" but not what that reads as is awkward to reconcile by hand.

The Indian grouping differs from plain English: one lakh = 100,000 and one crore
= 10,000,000, and the last three digits are grouped separately from the rest, so
1,234,567 reads as "twelve lakh thirty-four thousand five hundred and sixty
seven", not "one million two hundred and thirty-four thousand...".

Pure function, no framework imports, so it can be unit tested directly.

Usage:
    amount_in_words(Decimal("123456.75"))
    # "Rupees One Lakh Twenty Three Thousand Four Hundred Fifty Six and Seventy Five Paise Only"
"""

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

UNITS = [
    "",
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
    "Nine",
    "Ten",
    "Eleven",
    "Twelve",
    "Thirteen",
    "Fourteen",
    "Fifteen",
    "Sixteen",
    "Seventeen",
    "Eighteen",
    "Nineteen",
]
TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

# Place value and paise-per-rupee boundary (Indian numbering groups by hundreds).
_ONE_HUNDRED = 100


def _two_digits(n: int) -> str:
    if n < len(UNITS):
        return UNITS[n]
    return TENS[n // 10] + ((" " + UNITS[n % 10]) if n % 10 else "")


def _three_digits(n: int) -> str:
    """0-999, without a leading 'and'."""
    if n >= _ONE_HUNDRED:
        head = UNITS[n // 100] + " Hundred"
        rest = n % 100
        return head + ((" and " + _two_digits(rest)) if rest else "")
    return _two_digits(n)


def _below_hundred(n: int) -> str:
    """0-99. Used for the thousand/lakh/crore tails, which take 'and'."""
    return _two_digits(n)


def _int_to_words(n: int) -> str:
    """Convert a non-negative integer to Indian-grouped words."""
    if n == 0:
        return "Zero"
    if n < 0:
        n = -n

    crore = n // 10_000_000
    rest = n % 10_000_000
    lakh = rest // 100_000
    rest = rest % 100_000
    thousand = rest // 1000
    hundred = rest % 1000

    parts = []
    if crore:
        parts.append(f"{_int_to_words(crore)} Crore")
    if lakh:
        parts.append(f"{_below_hundred(lakh)} Lakh")
    if thousand:
        parts.append(f"{_below_hundred(thousand)} Thousand")
    if hundred:
        parts.append(_three_digits(hundred))
    return " ".join(p for p in parts if p)


def _round_paise(value: Decimal):
    """Split into whole rupees and paise, rounding half-up to 2dp.

    Rounding carries: 0.999 must read as "One Rupee and One Hundred Paise", not
    "Zero Rupees and One Hundred Paise".

    Note the explicit ROUND_DOWN when splitting. Decimal.to_integral_value()
    rounds by default (half-even), so 999.99 would become 1000 whole rupees and
    -1 paise. The half-up rounding has already happened in the quantize above;
    this split must only truncate.
    """
    quantised = Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    rupees = int(quantised.to_integral_value(rounding=ROUND_DOWN))
    paise = int(
        (quantised - quantised.to_integral_value(rounding=ROUND_DOWN))
        .scaleb(2)
        .to_integral_value(rounding=ROUND_DOWN)
    )
    if paise >= _ONE_HUNDRED:
        paise -= _ONE_HUNDRED
        rupees += 1
    return rupees, paise


def amount_in_words(amount) -> str:
    """Write a rupee amount out in words, Indian format.

    Returns a full sentence suitable for printing, always ending in 'Only'.
    Zero, negatives and unparseable input are handled rather than raising, since
    this is called while rendering a document and must never break the print.
    """
    try:
        value = Decimal(str(amount))
    except Exception:
        value = Decimal("0")
    # NaN and Infinity are valid Decimals that blow up on comparison and
    # quantize, so reject them before any arithmetic. This runs while rendering
    # a document and must never raise.
    if not value.is_finite():
        return "Rupees Zero Only"
    if value < 0:
        value = Decimal("0")

    rupees, paise = _round_paise(value)
    if rupees == 0 and paise == 0:
        return "Rupees Zero Only"

    out = [f"Rupees {_int_to_words(rupees)}"]
    if paise:
        out.append(f"and {_int_to_words(paise)} Paise")
    out.append("Only")
    return " ".join(out)
