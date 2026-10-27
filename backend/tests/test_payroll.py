"""Tests for payroll arithmetic.

The properties that matter:
  * net is derived from its inputs, never accepted as typed
  * an advance is always recovered, and recovery can never make net negative
  * a period is exact - an unreadable one is refused, not guessed
  * a period can be paid once, so the constraint belongs in the data

Mixed pure and database-backed: the arithmetic is pure, but the double-payment
guard is a database constraint and needs a real database to prove.

Run: python backend/tests/test_payroll.py
"""

import os
import sys
from datetime import date
from decimal import Decimal

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app.services import payroll as pr  # noqa: E402

D = Decimal


# --- the formula -----------------------------------------------------------


def test_net_is_basic_plus_allowances_less_deductions():
    r = pr.compute_net(10000, 2000, 1500)
    assert r["gross"] == D("12000.00"), r
    assert r["net_pay"] == D("10500.00"), r


def test_no_deductions_means_basic_plus_allowances():
    r = pr.compute_net(10000, 0, 0)
    assert r["net_pay"] == D("10000.00"), r


def test_advance_recovery_reduces_net():
    r = pr.compute_net(10000, 0, 0, advance_recovered=3000)
    assert r["net_pay"] == D("7000.00"), r
    assert r["advance_recovered"] == D("3000.00"), r


def test_deductions_and_advances_are_reported_separately():
    # Folding advance recovery into "deductions" would make the register
    # unreadable: a reader could not tell a fine from a salary advance.
    r = pr.compute_net(10000, 0, 500, advance_recovered=2000)
    assert r["deductions"] == D("500.00"), r
    assert r["advance_recovered"] == D("2000.00"), r
    assert r["total_debits"] == D("2500.00"), r
    assert r["net_pay"] == D("7500.00"), r


def test_decimal_inputs_do_not_drift():
    r = pr.compute_net("1000.10", "0.20", "0.30")
    assert r["net_pay"] == D("1000.00"), r
    assert r["net_pay"] == r["basic"] + r["allowances"] - r["deductions"]


def test_negative_inputs_are_refused():
    for kwargs in (
        {"basic": -1},
        {"allowances": -1},
        {"deductions": -1},
        {"advance_recovered": -1},
    ):
        try:
            pr.compute_net(**{"basic": 10000, "allowances": 0, "deductions": 0, **kwargs})
        except pr.PayrollError:
            continue
        raise AssertionError(f"{kwargs} should have been refused")


# --- net is computed, not typed -------------------------------------------


def test_a_matching_supplied_net_is_accepted():
    pr.validate_supplied_net(D("10500.00"), D("10500.00"))


def test_a_mismatched_net_is_refused():
    # The property the business asked for: the register must not be able to
    # hold a total that disagrees with its own inputs.
    try:
        pr.validate_supplied_net(D("10500.00"), D("11000.00"))
    except pr.PayrollError as e:
        assert "does not match the formula" in str(e)
        assert "10500" in str(e) and "11000" in str(e)
        return
    raise AssertionError("a mismatched net pay should have been refused")


def test_an_absent_net_is_fine():
    # Not supplying it is the normal case: the server computes it.
    pr.validate_supplied_net(D("10500.00"), None)
    pr.validate_supplied_net(D("10500.00"), "")


def test_a_one_paisa_mismatch_is_still_refused():
    # Tolerating "small" differences is how a register stops reconciling.
    try:
        pr.validate_supplied_net(D("10500.00"), D("10500.01"))
    except pr.PayrollError:
        return
    raise AssertionError("a one-paisa difference should still be refused")


def test_a_non_numeric_net_is_refused():
    for bad in ["abc", "ten thousand"]:
        try:
            pr.validate_supplied_net(D("10500.00"), bad)
        except pr.PayrollError:
            continue
        raise AssertionError(f"{bad!r} should have been refused")


# --- advance recovery ------------------------------------------------------


def test_recovery_is_capped_at_the_outstanding_amount():
    p = pr.plan_recovery(3000, 10000)
    assert p["advance_recovered"] == D("3000.00"), p
    assert p["advance_outstanding_after"] == D("0.00"), p
    assert p["carry_forward"] is False


def test_recovery_never_pushes_net_below_zero():
    # The tempting "deduct the full outstanding" gives a negative paycheque.
    p = pr.plan_recovery(8000, 6000)
    assert p["advance_recovered"] == D("6000.00"), p
    assert p["advance_outstanding_after"] == D("2000.00"), p
    assert p["carry_forward"] is True


def test_an_advance_larger_than_salary_carries_forward():
    p = pr.plan_recovery(50000, 10000)
    assert p["advance_recovered"] == D("10000.00"), p
    assert p["advance_outstanding_after"] == D("40000.00"), p
    assert p["carry_forward"] is True


def test_a_requested_smaller_amount_is_honoured():
    p = pr.plan_recovery(10000, 10000, requested=2500)
    assert p["advance_recovered"] == D("2500.00"), p
    assert p["advance_outstanding_after"] == D("7500.00"), p


def test_a_request_larger_than_possible_is_capped():
    p = pr.plan_recovery(3000, 5000, requested=99999)
    assert p["advance_recovered"] == D("3000.00"), p


def test_nothing_outstanding_means_no_recovery():
    p = pr.plan_recovery(0, 10000)
    assert p["advance_recovered"] == D("0.00"), p
    assert p["carry_forward"] is False


def test_zero_net_recovers_nothing():
    # A month with no net pay cannot service an advance.
    p = pr.plan_recovery(5000, 0)
    assert p["advance_recovered"] == D("0.00"), p
    assert p["advance_outstanding_after"] == D("5000.00"), p


def test_negative_outstanding_is_refused():
    try:
        pr.plan_recovery(-1, 10000)
    except pr.PayrollError:
        return
    raise AssertionError("a negative advance balance should have been refused")


def test_recovery_and_net_together_stay_consistent():
    # End to end: recovery is planned first, then fed into the formula.
    # basic 10000 + allowances 1000 - deductions 2000 = 9000 to work with, and
    # an advance of exactly 9000, so it is cleared in one period and net is 0.
    r = pr.compute_net(10000, 1000, 2000)
    assert r["net_pay"] == D("9000.00"), r
    plan = pr.plan_recovery(9000, r["net_pay"])
    assert plan["advance_recovered"] == D("9000.00"), plan
    final = pr.compute_net(10000, 1000, 2000, plan["advance_recovered"])
    assert final["net_pay"] == D("0.00"), final
    assert final["net_pay"] >= D("0.00")
    assert plan["advance_outstanding_after"] == D("0.00"), plan
    assert plan["carry_forward"] is False


def test_a_partly_recovered_advance_carries_the_remainder():
    # Net 4000 against an advance of 9000: 4000 recovered, 5000 still owed.
    r = pr.compute_net(10000, 1000, 7000)
    plan = pr.plan_recovery(9000, r["net_pay"])
    final = pr.compute_net(10000, 1000, 7000, plan["advance_recovered"])
    assert final["net_pay"] == D("0.00"), final
    assert plan["advance_recovered"] == D("4000.00"), plan
    assert plan["advance_outstanding_after"] == D("5000.00"), plan
    assert plan["carry_forward"] is True


# --- periods ---------------------------------------------------------------


def test_period_accepts_the_canonical_form():
    assert pr.normalise_period("2026-09") == "2026-09"
    assert pr.normalise_period(" 2026-09 ") == "2026-09"


def test_period_accepts_a_date_and_a_datetime():
    assert pr.normalise_period(date(2026, 9, 28)) == "2026-09"
    assert pr.normalise_period("2026-09-28") == "2026-09"


def test_an_unreadable_period_is_refused_not_guessed():
    # Falling back to "this month" would silently pay the wrong month.
    for bad in ["", None, "september", "26-9", "2026-13", "2026-00", "abcd-ef"]:
        try:
            pr.normalise_period(bad)
        except pr.PayrollError:
            continue
        raise AssertionError(f"{bad!r} should have been refused")


def test_period_bounds_cover_the_whole_month():
    start, end = pr.period_bounds("2026-09")
    assert start == date(2026, 9, 1)
    assert end == date(2026, 9, 30)


def test_period_bounds_handle_february_in_a_leap_year():
    assert pr.period_bounds("2024-02")[1] == date(2024, 2, 29)
    assert pr.period_bounds("2026-02")[1] == date(2026, 2, 28)


def test_period_rolls_over_the_year_end():
    assert pr.next_period("2026-12") == "2027-01"
    assert pr.previous_period("2026-01") == "2025-12"
    assert pr.previous_period("2026-09") == "2026-08"


def test_period_for_date_defaults_to_today():
    assert pr.period_for_date(date(2026, 9, 28)) == "2026-09"


# --- one payment per period (needs the database) ---------------------------


def test_a_period_cannot_be_paid_twice():
    # The real risk this guards: paying an employee twice for the same month.
    # salary_payments carries UNIQUE(employee_id, period_month) so the database
    # refuses it, not merely the UI.
    db = _db()
    e = _employee(db, "Asha")
    db.add(_payment(e, "2026-09"))
    db.commit()

    db.add(_payment(e, "2026-09"))
    try:
        db.commit()
    except Exception as exc:
        assert "UNIQUE" in str(exc).upper(), str(exc)
        return
    raise AssertionError("a duplicate period payment should have been refused")


def test_different_periods_are_allowed():
    db = _db()
    e = _employee(db, "Ravi")
    db.add(_payment(e, "2026-08"))
    db.add(_payment(e, "2026-09"))
    db.commit()
    assert db.query(__import__("backend.app.models", fromlist=["x"]).SalaryPayment).count() == 2


def test_two_employees_can_be_paid_for_the_same_period():
    db = _db()
    db.add(_payment(_employee(db, "Asha"), "2026-09"))
    db.add(_payment(_employee(db, "Ravi"), "2026-09"))
    db.commit()
    assert db.query(__import__("backend.app.models", fromlist=["x"]).SalaryPayment).count() == 2


def test_attendance_cannot_be_marked_twice_for_one_day():
    # Otherwise the same day is counted twice in any attendance report.
    db = _db()
    e = _employee(db, "Asha")
    db.add(_attendance(e, date(2026, 9, 28)))
    db.commit()
    db.add(_attendance(e, date(2026, 9, 28)))
    try:
        db.commit()
    except Exception as exc:
        assert "UNIQUE" in str(exc).upper(), str(exc)
        return
    raise AssertionError("a duplicate attendance row should have been refused")


# --- helpers ---------------------------------------------------------------


def _db():
    import weakref

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import backend.app.models  # noqa: F401
    from backend.app.core.database import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    # Dispose the engine when the session is reaped: an undisposed engine
    # leaves its raw sqlite3 connection to the garbage collector, which
    # surfaces as a ResourceWarning ("unclosed database") in the test output.
    weakref.finalize(session, engine.dispose)
    return session


def _employee(db, name):
    from backend.app import models

    e = models.Employee(
        employee_id=f"EMP-{name}",
        name=name,
        salary=10000,
        employee_type=models.EmployeeType.FULL_TIME,
    )
    db.add(e)
    db.commit()
    return e


def _payment(emp, period):
    from backend.app import models

    return models.SalaryPayment(
        employee_id=emp.id,
        payment_date=date(2026, 9, 28),
        period_month=period,
        basic_salary=10000,
        allowances=0,
        deductions=0,
        net_pay=10000,
    )


def _attendance(emp, on):
    from backend.app import models

    return models.Attendance(employee_id=emp.id, date=on, status="present")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"{len(tests)} tests passed")
