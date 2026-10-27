"""Shared pytest configuration and fixtures.

Two jobs:

1. Put the repo root on sys.path. The application is a source tree, not an
   installed distribution, so `import backend.app...` only resolves if the repo
   root is on the path. Doing it here means `pytest`, `python -m pytest
   backend/tests` and a bare `pytest backend/tests/test_foo.py` all work from
   anywhere.

2. Provide the in-memory database fixture. Every test that needs persistence
   uses this rather than building its own engine, which means the schema is
   created in exactly one place and a new table cannot be added to one test
   file and forgotten in another.

The single most important property of this suite: nothing here touches the real
database. `data/masala_factory.db` holds live business records, and the tests
would be a liability if a stray `sqlite3.connect` could reach it. Tests that
need to exercise HTTP or the real schema copy it to a temporary file first -
see `copy_of_real_db` - and even then they must not write to the original.
"""

import os
import shutil
import sqlite3
import sys
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import backend.app.models  # noqa: E402,F401  (registers every table on Base)
from backend.app.core.database import Base  # noqa: E402

# The real database. Referenced only to be guarded against, never written to.
REAL_DB = os.path.join(ROOT, "data", "masala_factory.db")


@pytest.fixture
def make_db():
    """Factory returning a brand new in-memory database, schema created.

    A factory rather than a single session because most tests want their own
    isolated database; sharing one session across tests makes order matter and
    lets one test's committed rows leak into the next.

    Engines are disposed at teardown: an undisposed engine leaves its raw
    sqlite3 connection to be reaped by the garbage collector, which surfaces
    as a ResourceWarning ("unclosed database") in the test output.
    """
    engines = []

    def _make():
        engine = create_engine("sqlite:///:memory:")
        engines.append(engine)
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()

    yield _make
    for engine in engines:
        engine.dispose()


@pytest.fixture
def db(make_db):
    """A single empty in-memory database, for the common case."""
    return make_db()


@pytest.fixture
def seq():
    """Monotonic counter for building unique business IDs (CH-10001, P-0001)."""
    counter = iter(range(1, 100000))

    def _next():
        return next(counter)

    return _next


@pytest.fixture
def temp_dir():
    """A temporary directory, removed afterwards."""
    d = tempfile.mkdtemp(prefix="smitest_")
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def copy_of_real_db(temp_dir):
    """A writable COPY of the real database, for schema/upgrade tests.

    Guarded deliberately: the copy is opened read-write, so if any code under
    test resolves the original path it would corrupt live records. The guard
    below re-checks the file's mtime and size after the test and fails loudly
    if anything touched the original.
    """
    if not os.path.exists(REAL_DB):
        pytest.skip("no real database present")
    copy = os.path.join(temp_dir, "copy.db")
    shutil.copy2(REAL_DB, copy)
    before = os.stat(REAL_DB)

    yield copy

    after = os.stat(REAL_DB)
    if (before.st_mtime, before.st_size) != (after.st_mtime, after.st_size):
        pytest.fail(f"the real database was modified by a test: {REAL_DB}")


@pytest.fixture(autouse=True)
def no_real_db_writes():
    """Fail any test that tries to write to the real database.

    The live file has no read-only flag we can rely on across the codebase, so
    this is a last-resort tripwire rather than a guarantee. It catches the
    common accident - a test opening the production path and committing - and
    turns silent data loss into a loud failure.
    """
    if not os.path.exists(REAL_DB):
        yield
        return

    before = os.stat(REAL_DB)
    yield
    after = os.stat(REAL_DB)
    if (before.st_mtime, before.st_size) != (after.st_mtime, after.st_size):
        pytest.fail(
            f"a test modified the real database {REAL_DB}\n"
            f"  before: size={before.st_size} mtime={before.st_mtime}\n"
            f"  after:  size={after.st_size} mtime={after.st_mtime}\n"
            f"Use the `db` fixture (in-memory) or `copy_of_real_db` instead."
        )


def table_count(path, table):
    """Row count for a table in an arbitrary SQLite file. Test helper."""
    c = sqlite3.connect(path)
    try:
        return c.execute(f"select count(*) from {table}").fetchone()[0]
    finally:
        c.close()
