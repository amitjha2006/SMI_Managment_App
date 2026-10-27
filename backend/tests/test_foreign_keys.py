"""Tests for foreign key enforcement.

SQLite ships with foreign keys OFF, so the schema's 58 constraints used to
describe relationships that nothing checked - a row could point at a parent
that no longer existed, and nothing would complain. These tests pin three
things:

  1. enforcement is actually ON on the app's engine (not merely declared)
  2. the real database holds no violating rows, which is what made turning it
     on safe; if that ever stops being true this fails loudly
  3. enforcement does what it says - an orphan write is now refused

Point 2 is the one that matters most. Enabling enforcement against a database
that already contains violations makes previously-valid operations fail, so
the audit is re-run here rather than trusted once and forgotten.

Run: python backend/tests/test_foreign_keys.py
"""

import os
import sys

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

REAL_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "masala_factory.db",
)

# pragma returns "NO ACTION" for an ordinary foreign key, and that is a truthy
# string, so counting it would claim every constraint cascades.
NOT_A_CASCADE = ("NO ACTION", "RESTRICT")


def _enforcement_is_on():
    from backend.app.core.database import engine

    with engine.connect() as conn:
        return conn.exec_driver_sql("PRAGMA foreign_keys").fetchone()[0] == 1


def test_foreign_key_enforcement_is_on_for_the_app_engine():
    assert _enforcement_is_on(), (
        "PRAGMA foreign_keys is OFF on the application engine, so every "
        "declared foreign key is decorative and rows can point at parents "
        "that do not exist"
    )


def test_every_declared_foreign_key_is_enforced():
    # Guard against a future engine being created without the listener, which
    # would silently turn the whole thing off again.
    import backend.app.models  # noqa: F401
    from backend.app.core.database import Base

    declared = sum(len(t.foreign_keys) for t in Base.metadata.tables.values())
    assert declared > 0, "no foreign keys are declared at all"
    assert _enforcement_is_on(), f"{declared} constraints declared but not enforced"


def test_the_real_database_has_no_violating_rows():
    # This is the precondition for enforcement being safe. If it ever fails,
    # some operation is about to start erroring on data that was already there.
    if not os.path.exists(REAL_DB):
        return  # nothing to audit
    import sqlite3

    con = sqlite3.connect(REAL_DB)
    try:
        con.execute("PRAGMA foreign_keys = ON")
        offenders = []
        for (child,) in con.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%'"
        ):
            for fk in con.execute(f"pragma foreign_key_list({child})"):
                parent = fk[2]
                ccol = fk[3]
                pcol = fk[4] or "id"
                n = con.execute(
                    f"select count(*) from {child} c where c.{ccol} is not null "
                    f"and not exists (select 1 from {parent} p where p.{pcol} = c.{ccol})"
                ).fetchone()[0]
                if n:
                    offenders.append(f"{child}.{ccol} -> {parent}.{pcol}: {n} row(s)")
    finally:
        con.close()
    assert not offenders, (
        "the real database has rows violating a foreign key, so enforcement "
        "would break existing operations: " + "; ".join(offenders)
    )


def _enforcing_file_db():
    """A temporary database on an engine configured like the real one.

    The shared `db` fixture builds its own engine, and the pragma listener is
    bound to the application engine specifically - so a test that wants to
    observe enforcement happening has to build an engine that has it too.
    """
    import os as _os
    import tempfile

    from sqlalchemy import create_engine, event

    import backend.app.models  # noqa: F401
    from backend.app.core.database import Base

    path = _os.path.join(tempfile.mkdtemp(prefix="smifk_"), "fk.db")
    eng = create_engine(f"sqlite:///{path}")

    @event.listens_for(eng, "connect")
    def _on(dbapi_connection, _record):  # mirrors database.py
        cur = dbapi_connection.cursor()
        try:
            cur.execute("PRAGMA foreign_keys = ON")
        finally:
            cur.close()

    Base.metadata.create_all(eng)
    return eng


def test_an_orphan_row_is_now_refused():
    # The point of enforcement: a child that points at a missing parent is
    # rejected rather than silently stored.
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    eng = _enforcing_file_db()
    try:
        refused = False
        with eng.begin() as con:
            try:
                # product_id points at a product that does not exist. The other
                # NOT NULL columns are filled with placeholders because the point
                # of the insert is the dangling reference, not the other values.
                con.execute(
                    text(
                        "insert into production_entries (product_id, packaging_size_id, "
                        "production_date, production_number, status, created_at) "
                        "values (999999, 999999, datetime('now'), 'P-1', 'draft', datetime('now'))"
                    )
                )
            except IntegrityError:
                refused = True
    finally:
        eng.dispose()
    assert refused, "an orphaned row was accepted despite PRAGMA foreign_keys=ON"


def test_deleting_a_referenced_parent_is_refused():
    import datetime as _dt

    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    eng = _enforcing_file_db()
    try:
        with eng.begin() as con:
            con.execute(
                text(
                    "insert into customers (customer_id, name, contact_number, created_at) "
                    "values ('C-1','Test','9999999999',:t)"
                ),
                {"t": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")},            )
            cid = con.execute(text("select id from customers where customer_id='C-1'")).scalar()
            con.execute(
                text(
                    "insert into sales_challans (challan_number, challan_date, customer_id, "
                    "status, total_weight, total_price, paid_amount, outstanding_amount, "
                    "is_archived, created_at) values ('CH-1', datetime('now'), :c, 'draft', "
                    "0,0,0,0,0, datetime('now'))"
                ),
                {"c": cid},
            )

        refused = False
        with eng.begin() as con:
            try:
                con.execute(text("delete from customers where id=:c"), {"c": cid})
            except IntegrityError:
                refused = True
    finally:
        eng.dispose()
    assert refused, (
        "a parent row was deleted while a challan still referenced it, leaving "
        "an orphan - exactly what enabling enforcement is meant to prevent"
    )


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"{len(tests)} tests passed")
