"""Sequential document numbering for challans and sales invoices.

Why this exists
---------------
Challan numbers used to be generated at random:

    CH-{random.randint(10000, 99999)}      # retried until unused

That is acceptable for a delivery note and unacceptable for a tax invoice.
GST practice expects a document series to be sequential, because a GAP in the
sequence is the classic signal of a missing or suppressed document, and a gap
can stop the buyer's accountant from claiming input credit. A random series
guarantees gaps.

It is also just bad bookkeeping: `CH-45181` dated 20-Sep sits next to
`CH-51649` dated 09-Sep, so the register does not sort into the order the
documents were issued.

The format
----------
    CH/26-27/0001        delivery challan
    INV/26-27/0001       sales tax invoice

An Indian financial year runs 1 April to 31 March, so a challan raised on
28-Sep-2026 is series 26-27. Series-per-year is the convention tax books use,
and it means each year's register is self-contained and easy to hand to an
auditor.

The new format deliberately does NOT resemble the legacy `CH-45181` form.
That is what lets the 89 existing challans keep their real numbers untouched
while the new series starts cleanly at 0001 - no renumbering, no collision,
and no rewriting of an already-delivered document.

Gaplessness
-----------
The counter is a stored value, not `MAX(number) + 1`. Deriving it from the
existing rows would reintroduce gaps the moment a document were deleted or
archived - exactly when a gap looks most suspicious.

The increment is a single atomic upsert (INSERT ... ON CONFLICT DO UPDATE ...
RETURNING), so two requests arriving together cannot both read the same
value and produce the same number. It is also monotonic: a number is never
reissued, even after the document using it is removed.
"""

import re
from datetime import date, datetime

from sqlalchemy import text

# Indian financial year: 1 April to 31 March.
FY_START_MONTH = 4

_SERIES_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/.-]{0,29}$")


class SequenceError(ValueError):
    """A series key or prefix that cannot produce a usable document number."""


from backend.app.core.database import _ist_wall


def financial_year(d: date) -> str:
    """Financial-year series label for a date, e.g. 28-Sep-2026 -> '26-27'.

    On or after April the year starts this calendar year; before April it
    started the previous one.
    """
    if d is None:
        d = date.today()
    if isinstance(d, datetime):
        d = d.date()
    start = d.year if d.month >= FY_START_MONTH else d.year - 1
    return f"{start % 100:02d}-{(start + 1) % 100:02d}"


def format_number(prefix: str, fy: str, sequence: int) -> str:
    """Build the human-facing document number, e.g. CH/26-27/0001.

    The sequence is zero-padded to 4 for readability but NOT truncated, so
    the 10001st document becomes .../10001 rather than colliding with 0001.
    """
    if not prefix or not _SERIES_RE.match(prefix):
        raise SequenceError(
            f"Document prefix {prefix!r} is not usable. Use up to 30 letters, "
            f"digits, dot, hyphen or slash."
        )
    if sequence < 1:
        raise SequenceError(f"Sequence must start at 1, got {sequence}.")
    return f"{prefix}/{fy}/{sequence:04d}"


def next_number(db, prefix: str, doc_date: date, table_name: str = None) -> str:
    """Reserve and return the next number in the series for this financial year.

    `table_name`, when given, is the table whose existing numbers should be
    checked against the freshly issued one. Uniqueness is also enforced by a
    database constraint, so this check is a courtesy that turns an impossible
    clash into a clear error rather than an IntegrityError traceback.
    """
    fy = financial_year(doc_date)
    key = f"{prefix.upper()}|{fy}"

    # Atomic: one statement both creates the row and bumps the counter, and
    # RETURNING hands back the value it landed on. No read-then-write window,
    # so two simultaneous requests get 0001 and 0002, never 0001 twice.
    row = db.execute(
        _upsert_sql(),
        {"key": key},
    ).scalar_one()
    sequence = int(row)

    number = format_number(prefix.upper(), fy, sequence)

    if table_name:
        # Defensive only. The series format cannot collide with legacy numbers
        # unless someone hand-enters one, which the Excel import path allows.
        column = _text_col(table_name)
        for _ in range(5):
            clash = db.execute(
                _text(f"select 1 from {table_name} where {column} = :n limit 1"),
                {"n": number},
            ).first()
            if not clash:
                return number
            sequence += 1
            number = format_number(prefix.upper(), fy, sequence)
        raise SequenceError(
            f"Could not find a free number in series {key} - the last five "
            f"were already present in {table_name}. Check for hand-entered "
            f"numbers in this series."
        )
    return number


# --- small SQL helpers, kept here so the atomic statement stays readable ---


def next_challan_number(db, doc_date: date = None) -> str:
    return next_number(db, "CH", doc_date or _ist_wall().date(), "sales_challans")


def next_invoice_number(db, doc_date: date = None) -> str:
    return next_number(db, "INV", doc_date or _ist_wall().date(), "sales_invoices")


def next_purchase_invoice_number(db, doc_date: date = None) -> str:
    return next_number(db, "PIN", doc_date or _ist_wall().date(), "purchase_invoices")


def next_production_number(db, doc_date: date = None) -> str:
    return next_number(db, "PR", doc_date or _ist_wall().date(), "production_entries")


def _text_col(table_name: str) -> str:
    # challans and invoices both store their number in *_number.
    if "challan" in table_name:
        return "challan_number"
    if "production" in table_name:
        return "production_number"
    return "invoice_number"


def _text(sql: str) -> str:

    return text(sql)


def _upsert_sql():

    return text(
        "insert into document_sequences (key, last_value) values (:key, 1) "
        "on conflict(key) do update set last_value = last_value + 1 "
        "returning last_value"
    )
