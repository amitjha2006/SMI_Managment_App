"""Payroll arithmetic.

The business confirmed a simple formula:

    net pay = basic + allowances - deductions

with one non-negotiable property: net is COMPUTED, never accepted as typed. A
hand-entered net that disagrees with the formula is refused, because a payroll
register where the total silently differs from its own inputs is a document
nobody can reconcile.

Advance recovery
----------------
An advance that is never recovered is not an advance, it is a gift. So the
payroll run also recovers outstanding advances, as a component that is reported
SEPARATELY rather than folded into "deductions":

    net = basic + allowances - deductions - advance_recovered

Recovery is capped so that net can never go below zero. An employee who owes
800 on advance against a 6,000 salary has 600 recovered and 200 carried, not a
negative paycheque.

The cap is why this lives in code rather than being done by eye: the tempting
"just deduct the full outstanding" produces a negative net, which then gets
"fixed" by someone typing a different number, and the register stops
reconciling to itself.
"""

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

TWO = Decimal("0.01")
ZERO = Decimal("0.00")

# Period format is a plain YYYY-MM string: sorts chronologically as text, and is
# unambiguous in a report column.
PERIOD_LEN = 7
MONTHS_PER_YEAR = 12


class PayrollError(ValueError):
    """A payroll figure that cannot be used."""


def _d(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return ZERO
    return Decimal(str(value))


def _q(value: Decimal) -> Decimal:
    return value.quantize(TWO, rounding=ROUND_HALF_UP)


def normalise_period(value) -> str:
    """Accept '2026-09', a date, or a datetime and return 'YYYY-MM'.

    A payroll period has to be exact. Falling back to "this month" when the
    input is unreadable would silently pay the wrong month, so an unparseable
    value is refused rather than guessed.
    """
    if value is None or value == "":
        raise PayrollError("A payroll period is required, in the form 2026-09.")
    if isinstance(value, (date, datetime)):
        return f"{value.year:04d}-{value.month:02d}"
    s = str(value).strip()
    if len(s) == PERIOD_LEN and s[4] == "-" and s[:4].isdigit() and s[5:].isdigit():
        month = int(s[5:])
        if not 1 <= month <= MONTHS_PER_YEAR:
            raise PayrollError(f"{s!r} is not a real month.")
        return s
    # tolerate a full ISO date or datetime
    try:
        return f"{datetime.fromisoformat(s.replace('Z', '+00:00')):%Y-%m}"
    except Exception as e:
        raise PayrollError(f"Could not read {value!r} as a payroll period. Use 2026-09.") from e


def period_bounds(period: str):
    """First and last day of a payroll period, as dates."""
    p = normalise_period(period)
    year, month = int(p[:4]), int(p[5:])
    start = date(year, month, 1)
    end_day = 31
    while True:
        try:
            return start, date(year, month, end_day)
        except ValueError:
            end_day -= 1


def compute_net(basic, allowances, deductions, advance_recovered=None) -> dict:
    """The payroll formula. Returns the inputs alongside the result.

    advance_recovered defaults to 0 and is passed in already capped by
    plan_recovery - this function does the arithmetic, the caller does the
    policy.
    """
    b = _d(basic)
    a = _d(allowances)
    d = _d(deductions)
    adv = _d(advance_recovered)

    for label, v in (("basic", b), ("allowances", a)):
        if v < ZERO:
            raise PayrollError(f"{label} cannot be negative (got {v}).")
    if d < ZERO:
        raise PayrollError(f"deductions cannot be negative (got {d}).")
    if adv < ZERO:
        raise PayrollError(f"advance recovery cannot be negative (got {adv}).")

    gross = b + a
    net = gross - d - adv
    return {
        "basic": _q(b),
        "allowances": _q(a),
        "gross": _q(gross),
        "deductions": _q(d),
        "advance_recovered": _q(adv),
        "total_debits": _q(d + adv),
        "net_pay": _q(net),
    }


def plan_recovery(advance_outstanding, net_before_recovery, requested=None) -> dict:
    """Decide how much of an outstanding advance to recover this period.

    Rules, in order:
      1. never more than requested (if the caller asked for a specific amount)
      2. never more than is outstanding
      3. never enough to push net below zero

    Returns the recovery plus what is left outstanding afterwards, so the
    register shows the employee still owes the remainder.
    """
    outstanding = _d(advance_outstanding)
    net = _d(net_before_recovery)
    if outstanding < ZERO:
        raise PayrollError("Advance outstanding cannot be negative.")

    recoverable = min(outstanding, max(net, ZERO))
    if requested is not None:
        want = _d(requested)
        if want < ZERO:
            raise PayrollError("Requested advance recovery cannot be negative.")
        recoverable = min(recoverable, want)

    recovered = _q(recoverable)
    return {
        "advance_outstanding_before": _q(outstanding),
        "advance_recovered": recovered,
        "advance_outstanding_after": _q(outstanding - recovered),
        # True when the advance cannot be finished this period, so the UI can
        # say "200 still to recover" instead of implying it is settled.
        "carry_forward": outstanding - recovered > ZERO,
    }


def validate_supplied_net(computed_net, supplied_net) -> None:
    """Refuse a hand-typed net that disagrees with the formula.

    This is the check the business asked for. Tolerating a small mismatch would
    defeat it: the whole reason to compute net is that the register cannot hold
    a total which quietly disagrees with its own inputs.
    """
    if supplied_net is None or supplied_net == "":
        return  # not supplied: nothing to check
    try:
        want = _d(supplied_net)
    except Exception as e:
        raise PayrollError("Net pay must be a number.") from e
    if _q(want) != _q(_d(computed_net)):
        raise PayrollError(
            f"Net pay does not match the formula. Basic + allowances - "
            f"deductions - advance recovered works out to {computed_net}, "
            f"but {want} was entered. Payroll totals are calculated, not typed."
        )


def period_for_date(d=None) -> str:
    """The period a given date falls in, for defaulting the payroll form."""
    d = d or date.today()
    return f"{d.year:04d}-{d.month:02d}"


def next_period(period: str) -> str:
    p = normalise_period(period)
    year, month = int(p[:4]), int(p[5:])
    month += 1
    if month > MONTHS_PER_YEAR:
        month, year = 1, year + 1
    return f"{year:04d}-{month:02d}"


def previous_period(period: str) -> str:
    p = normalise_period(period)
    year, month = int(p[:4]), int(p[5:])
    month -= 1
    if month < 1:
        month, year = 12, year - 1
    return f"{year:04d}-{month:02d}"
