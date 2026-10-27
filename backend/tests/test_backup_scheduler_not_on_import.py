"""Importing the backend must not start background jobs.

The auto-backup scheduler was started by a module-level call at the bottom of
backend/main.py. Every process that imported the module therefore got a daemon
thread that ticks every ten minutes and, once the wall clock reached the
configured backup hour, copies the live database and inserts a backup row.

That produced two real failures, neither of which looked related to backups:

* conftest's autouse `no_real_db_writes` tripwire failed the suite whenever the
  run happened to be the first process of the day past the backup hour, so
  `python -m pytest backend/tests` failed intermittently and the reason
  ("a test modified the real database") pointed at the wrong place.
* A plain test run created backup files in data/backups.

The scheduler must start when the app is served, via the lifespan handler,
and not on import.

Run: python -m pytest backend/tests/test_backup_scheduler_not_on_import.py
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = r"C:\Shresth_Masala_Application"


def _run_python(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def test_importing_backend_does_not_start_the_scheduler():
    """The regression itself, checked in a clean subprocess.

    A subprocess is required: by the time this test runs, backend.main has
    already been imported by other test modules, so an in-process check could
    not tell a fresh import from an already-started scheduler.
    """
    code = (
        "import backend.main as m;"
        "print('STARTED' if m._auto_backup_started else 'NOT_STARTED')"
    )
    r = _run_python(code)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "NOT_STARTED" in r.stdout, (
        "importing backend.main started the auto-backup scheduler; any "
        "process that imports it (including pytest) can write to the live "
        f"database. Got: {r.stdout.strip()}"
    )


def test_scheduler_is_wired_to_the_lifespan_handler_instead():
    """Deleting the eager call must not delete the scheduler with it."""
    src = (Path(ROOT) / "backend" / "main.py").read_text(encoding="utf-8")
    # Exactly one definition, and the eager top-level call is gone.
    assert src.count("def start_auto_backup_scheduler(") == 1
    assert "lifespan=_lifespan" in src, (
        "the app is not wired to the lifespan handler, so the packaged app "
        "would no longer take automatic backups"
    )
    assert "async def _lifespan(" in src
    assert "start_auto_backup_scheduler()" in src, (
        "the lifespan handler must start the scheduler, or automatic daily "
        "backups silently stop happening for real users"
    )


def test_no_bare_scheduler_call_at_module_level():
    """Parsed rather than grepped: the call must be nested inside the lifespan
    function, never a statement in the module body itself."""
    import ast

    src = (Path(ROOT) / "backend" / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    for node in tree.body:  # module level only, not inside any function
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "start_auto_backup_scheduler"
        ):
            pytest.fail(
                "start_auto_backup_scheduler() is called at module level; "
                "importing the app would start background jobs again"
            )

    # And it must actually be called from inside the lifespan handler.
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_lifespan":
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Name)
                    and sub.func.id == "start_auto_backup_scheduler"
                ):
                    found = True
    assert found, (
        "the lifespan handler does not start the scheduler, so automatic "
        "daily backups would silently stop for real users"
    )


def test_fastapi_is_not_using_the_deprecated_on_event_api():
    """on_event is deprecated in this FastAPI version; lifespan is the
    supported way and emits no warning on every startup."""
    src = (Path(ROOT) / "backend" / "main.py").read_text(encoding="utf-8")
    assert "@app.on_event(" not in src, (
        "use the lifespan handler instead of the deprecated on_event API"
    )


def test_a_test_run_leaves_no_new_backup_files(tmp_path):  # noqa: ARG001 - pytest fixture, asserts on disk state not the dir itself
    """End-to-end: importing the app must not produce backups on disk."""
    db = Path(ROOT) / "data" / "masala_factory.db"
    backups = Path(ROOT) / "data" / "backups"
    before = (
        sorted(p.name for p in backups.glob("*")) if backups.exists() else []
    )
    r = _run_python("import backend.main")
    assert r.returncode == 0, r.stderr[-2000:]
    after = sorted(p.name for p in backups.glob("*")) if backups.exists() else []
    assert before == after, (
        f"importing the backend created backup files: {set(after) - set(before)}"
    )
    assert db.exists(), "the live database should still be where it was"
