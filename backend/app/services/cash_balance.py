"""Cash float balance for customers and suppliers.

The balance is DERIVED, never stored as a running total:

    balance = opening_float + sum( received ) - sum( paid )

ignoring voided movements.

Why derived rather than a stored running figure: a stored total is only ever
correct right up to the first mistake, and a wrong total is invisible -
nothing to compare it against. With every event kept as a row, the balance can
be recomputed at any time, a bad entry is voided rather than deleted, and two
people editing at once cannot silently overwrite each other.

Direction is stated from the FACTORY's point of view, which is the only
framing that stays unambiguous when the same code serves both customers and
suppliers:

    received - they handed us cash. Balance rises.
    paid     - we handed them cash. Balance falls.

Pure arithmetic plus a small amount of row filtering, so it can be tested
without a database for the parts that matter.
"""

from decimal import Decimal

ZERO = Decimal("0.00")
TWO = Decimal("0.01")

RECEIVED = "received"
PAID = "paid"
MOVEMENT_TYPES = (RECEIVED, PAID)

# received adds, paid subtracts.
SIGN = {RECEIVED: Decimal(1), PAID: Decimal(-1)}


def _d(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return ZERO
    return Decimal(str(value))


def compute_balance(opening_float, movements) -> Decimal:
    """Balance from an opening float plus an iterable of movement rows.

    `movements` may be ORM rows or plain objects with `amount`,
    `movement_type` and `is_void`. Voided rows are skipped so undoing an entry
    is a flag flip rather than a destructive delete.
    """
    total = _d(opening_float)
    for m in movements:
        if getattr(m, "is_void", False):
            continue
        sign = SIGN.get(getattr(m, "movement_type", None))
        if sign is None:
            # An unrecognised type would silently vanish from the arithmetic.
            # Raise instead: a balance that quietly ignores a row is worse
            # than a request that errors.
            raise ValueError(
                f"Unknown cash movement type {getattr(m, 'movement_type', None)!r}; "
                f"expected one of {MOVEMENT_TYPES}."
            )
        total += sign * _d(getattr(m, "amount", 0))
    return total.quantize(TWO)


def movement_signed_amount(movement_type, amount) -> Decimal:
    """The effect one movement has on the balance, signed."""
    if movement_type not in SIGN:
        raise ValueError(
            f"Unknown cash movement type {movement_type!r}; expected one of {MOVEMENT_TYPES}."
        )
    return (SIGN[movement_type] * _d(amount)).quantize(TWO)


def validate_movement(movement_type, amount) -> Decimal:
    """Validate a movement before it is written.

    A zero or negative amount is refused: 'received -50' has no meaning here,
    and accepting it would let a typo invert the direction of a real entry.
    """
    if movement_type not in SIGN:
        raise ValueError(
            f"Unknown cash movement type {movement_type!r}; expected one of {MOVEMENT_TYPES}."
        )
    amt = _d(amount)
    if amt <= ZERO:
        raise ValueError("Amount must be greater than zero.")
    return amt.quantize(TWO)
