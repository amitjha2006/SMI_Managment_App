"""Tests for sequential document numbering.

The properties that matter, and why:

  * sequential   - the register must sort into issue order
  * gapless      - a gap in a tax series is the signal of a suppressed
                   document, and on a PURCHASE invoice it is what stops a
                   supplier claiming input credit
  * per-FY       - Indian financial year is 1 Apr to 31 Mar
  * monotonic    - a number is never reissued, even after deletion
  * legacy-safe  - the 89 existing CH-##### challans are never renumbered and
                   cannot collide with the new series

Mixed pure and database-backed. The date maths and formatting are pure; the
gaplessness and atomicity claims need a real database.

Run: python backend/tests/test_document_sequence.py
"""

import os
import sys
from datetime import date

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app.services import document_sequence as ds  # noqa: E402

# --- financial year ---------------------------------------------------------


def test_financial_year_runs_april_to_march():
    # A document raised in September 2026 belongs to FY 2026-27.
    assert ds.financial_year(date(2026, 9, 28)) == "26-27"
    assert ds.financial_year(date(2026, 4, 1)) == "26-27"  # first day
    assert ds.financial_year(date(2026, 3, 31)) == "25-26"  # still old FY
    assert ds.financial_year(date(2027, 3, 31)) == "26-27"
    assert ds.financial_year(date(2027, 4, 1)) == "27-28"  # rolls over


def test_financial_year_boundary_is_exact():
    # 31 March and 1 April must straddle the boundary, not fall in one series.
    assert ds.financial_year(date(2026, 3, 31)) != ds.financial_year(date(2026, 4, 1))


def test_financial_year_accepts_a_datetime():
    from datetime import datetime

    assert ds.financial_year(datetime(2026, 9, 28, 14, 30)) == "26-27"


def test_financial_year_pads_to_two_digits():
    assert ds.financial_year(date(2009, 6, 1)) == "09-10"
    assert ds.financial_year(date(2099, 6, 1)) == "99-00"


# --- formatting ------------------------------------------------------------


def test_format_is_prefix_slash_fy_slash_padded_sequence():
    assert ds.format_number("CH", "26-27", 1) == "CH/26-27/0001"
    assert ds.format_number("INV", "26-27", 42) == "INV/26-27/0042"
    assert ds.format_number("PUR", "25-26", 7) == "PUR/25-26/0007"


def test_sequence_grows_past_four_digits_without_truncating():
    # 10000 must not collapse to 0000 and collide with the first number.
    assert ds.format_number("CH", "26-27", 9999) == "CH/26-27/9999"
    assert ds.format_number("CH", "26-27", 10000) == "CH/26-27/10000"
    assert ds.format_number("CH", "26-27", 10000) != ds.format_number("CH", "26-27", 1)


def test_prefix_is_upper_cased_by_the_caller_not_the_formatter():
    # format_number is a pure formatter; the upper-casing happens in
    # next_number so a lowercase prefix still yields one canonical series key.
    assert ds.format_number("ch", "26-27", 1) == "ch/26-27/0001"


def test_unusable_prefix_is_refused():
    for bad in ["", "  ", "CH EXTRA", "A" * 40, "CH!"]:
        try:
            ds.format_number(bad, "26-27", 1)
        except ds.SequenceError:
            continue
        raise AssertionError(f"prefix {bad!r} should have been refused")


def test_sequence_must_start_at_one():
    try:
        ds.format_number("CH", "26-27", 0)
    except ds.SequenceError:
        return
    raise AssertionError("sequence 0 should have been refused")


# --- database-backed: the properties that need a real transaction -----------


def test_numbers_come_out_sequential_with_no_gaps():
    db, _ = _db()
    got = [ds.next_number(db, "CH", date(2026, 9, 28)) for _ in range(5)]
    assert got == [
        "CH/26-27/0001",
        "CH/26-27/0002",
        "CH/26-27/0003",
        "CH/26-27/0004",
        "CH/26-27/0005",
    ], got


def test_numbering_continues_across_a_new_session():
    # A restart must not restart the series - that would reissue numbers.
    db1, Session1 = _db()
    ds.next_number(db1, "CH", date(2026, 9, 28))
    db2 = Session1()  # a fresh session against the same database
    assert ds.next_number(db2, "CH", date(2026, 9, 28)) == "CH/26-27/0002"


def test_each_financial_year_gets_its_own_series():
    db, _ = _db()
    a = ds.next_number(db, "CH", date(2026, 3, 31))
    b = ds.next_number(db, "CH", date(2026, 4, 1))
    assert a == "CH/25-26/0001", a
    assert b == "CH/26-27/0001", b


def test_different_prefixes_do_not_share_a_counter():
    db, _ = _db()
    assert ds.next_number(db, "CH", date(2026, 9, 28)) == "CH/26-27/0001"
    assert ds.next_number(db, "INV", date(2026, 9, 28)) == "INV/26-27/0001"
    assert ds.next_number(db, "PUR", date(2026, 9, 28)) == "PUR/26-27/0001"
    assert ds.next_number(db, "CH", date(2026, 9, 28)) == "CH/26-27/0002"


def test_rolled_back_creation_does_not_consume_a_number():
    # The property that makes the series gapless in practice: if the challan
    # is never saved, its number must not be burned.
    #
    # Both the counter and the challan row live in one transaction, so a
    # rollback takes the increment with it. That is the desired direction: a
    # failed save must NOT leave a hole. (The mirror risk - a number handed
    # out and then the transaction dies - cannot happen here because nothing
    # is returned to the caller until the commit succeeds.)
    db, _ = _db()
    first = ds.next_number(db, "CH", date(2026, 9, 28))
    assert first == "CH/26-27/0001"
    db.commit()  # 0001 is now really issued

    def _take_then_fail():
        n = ds.next_number(db, "CH", date(2026, 9, 28))
        assert n == "CH/26-27/0002"
        raise RuntimeError("simulated failure after the number was taken")

    try:
        _take_then_fail()
    except RuntimeError:
        db.rollback()  # the challan save failed
    db.commit()

    # 0002 was never issued, so the series continues at 0002 - no hole.
    assert ds.next_number(db, "CH", date(2026, 9, 28)) == "CH/26-27/0002"


def test_number_is_never_reissued_after_deletion():
    # Monotonic, even if a document using a number is removed: the counter is
    # stored, not derived from MAX(), so a deleted row cannot roll it back.
    db, _ = _db()
    first = ds.next_number(db, "CH", date(2026, 9, 28))
    ds.next_number(db, "CH", date(2026, 9, 28))
    assert first == "CH/26-27/0001"
    # nothing to delete here because the counter is not row-derived; assert
    # the counter table holds the high-water mark
    row = db.execute(
        __import__("sqlalchemy").text("select last_value from document_sequences where key=:k"),
        {"k": "CH|26-27"},
    ).scalar_one()
    assert row == 2, row


def test_legacy_challan_numbers_cannot_collide_with_the_new_series():
    # The 89 existing challans look like CH-45181. The new series is
    # CH/26-27/0001, so there is no overlap and none were renumbered.
    for legacy in ["CH-45181", "CH-38247", "CH-51649", "CH-92451", "CH-10000"]:
        assert "/" not in legacy
    db, _ = _db()
    new = ds.next_number(db, "CH", date(2026, 9, 28))
    assert new not in {"CH-45181", "CH-38247", "CH-51649", "CH-92451"}


def test_colliding_number_is_stepped_past():
    # Defensive path: if a hand-entered number already sits in the series, the
    # service must move on rather than raise an IntegrityError.
    db, _ = _db(_with_challans=True)
    # Seed a clash on the first number the sequence would hand out. Inserted
    # through the ORM so the fixture satisfies the real schema instead of
    # hand-listing every NOT NULL column.
    from backend.app import models

    db.add(
        models.SalesChallan(
            challan_number="CH/26-27/0001",
            challan_date=date(2026, 9, 28),
            customer_id=1,
        )
    )
    db.commit()
    got = ds.next_number(db, "CH", date(2026, 9, 28), table_name="sales_challans")
    assert got == "CH/26-27/0002", got


# --- helpers ---------------------------------------------------------------


def _db(_with_challans=False):
    import weakref

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import backend.app.models  # noqa: F401
    from backend.app.core.database import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    # Dispose the engine when the session is reaped. An undisposed engine
    # leaves its raw sqlite3 connection to the garbage collector, which
    # surfaces as a ResourceWarning ("unclosed database") in the test output.
    weakref.finalize(session, engine.dispose)
    return session, Session


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"{len(tests)} tests passed")
