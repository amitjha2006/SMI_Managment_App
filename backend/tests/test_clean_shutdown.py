"""Clean shutdown and Pydantic v2 config migration.

Two defects, both cosmetic-but-real, both of which would hide a genuine
problem later:

1. `ResourceWarning: unclosed database`. The engine is built on a StaticPool,
   which holds one sqlite3 connection open for the whole process. Nothing ever
   called engine.dispose(), so the connection was reclaimed by the garbage
   collector during interpreter shutdown and Python complained about it. The
   handle is closed by the OS either way, so no data was at risk - but a clean
   exit that prints a warning is indistinguishable, to whoever sees it, from an
   exit that actually leaked something.

2. `PydanticDeprecatedSince20: Support for class-based config`. 33 nested
   `class Config` blocks (32 in app/schemas/__init__.py, 1 in core/config.py)
   emitted a deprecation warning on every single import, including inside the
   packaged app. Pydantic v3 removes the syntax.

These are subprocess tests on purpose. By the time pytest runs, backend.main is
already imported and the engine already exists in this process, so an
in-process check could not distinguish a fresh import from a warm one.

Run: python -m pytest backend/tests/test_clean_shutdown.py
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"C:\Shresth_Masala_Application")
SCHEMAS = ROOT / "backend" / "app" / "schemas" / "__init__.py"
CONFIG = ROOT / "backend" / "app" / "core" / "config.py"


def _run(code: str, timeout: int = 240) -> subprocess.CompletedProcess:
    # Boot the subprocess against a throwaway database, never the live one.
    # backend.main binds its engine to settings.DATABASE_URL at import, and
    # the lifespan scheduler tick writes an auto-backup row wherever that
    # points - at the live file that write trips the no-real-db-writes
    # tripwire (same size, bumped mtime) depending on scheduler timing.
    import os
    import shutil
    import tempfile

    tmpdir = tempfile.mkdtemp(prefix="shutdowntest_")
    # The working directory is the throwaway dir, not the repo root: the
    # default backup dir is the relative path data/backups, so a boot that
    # runs here cannot litter the real folder with auto_*.db files (only
    # DATABASE_URL was redirected before, and the backup file lands before
    # any database write). Imports still resolve via PYTHONPATH.
    env = dict(
        os.environ,
        DATABASE_URL="sqlite:///" + str(Path(tmpdir, "test.db").as_posix()),
        SECRET_KEY="test-only-subprocess-secret-0123456789abcdef",
        PYTHONPATH=str(ROOT)
        + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else ""),
    )
    try:
        return subprocess.run(
            [sys.executable, "-X", "dev", "-c", code],
            cwd=tmpdir,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_import_raises_no_pydantic_class_config_deprecation():
    r = _run("import backend.main")
    assert r.returncode == 0, r.stderr[-2000:]
    assert "PydanticDeprecatedSince20" not in r.stderr, (
        "class-based Pydantic config is still in use:\n"
        + "\n".join(
            line for line in r.stderr.splitlines()
            if "PydanticDeprecated" in line
        )
    )


def test_no_class_config_blocks_remain():
    """The migration must be complete, not partial."""
    offenders = []
    for path in (ROOT / "backend").rglob("*.py"):
        if re.search(r"^[ \t]*class Config:", path.read_text(encoding="utf-8"), re.M):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"class-based config still present in: {offenders}"


def test_all_34_schema_configs_were_migrated():
    """Pins the count, so a schema added with the old spelling cannot slip in."""
    src = SCHEMAS.read_text(encoding="utf-8")
    migrated = len(re.findall(
        r"^[ \t]*model_config = ConfigDict\(from_attributes=True\)[ \t]*$",
        src, re.M))
    assert migrated == 34, (
        f"expected 34 migrated schema configs, found {migrated}"
    )
    assert "ConfigDict" in src.split("__all__")[0], "ConfigDict is not imported"


def test_from_attributes_behaviour_is_preserved():
    """The reason the migration could break things.

    from_attributes lets Pydantic read plain SQLAlchemy model instances. If a
    schema silently lost it, every response model would raise at runtime
    instead of returning rows - so assert it on the real classes, not the
    source text.
    """
    code = (
        "import json, backend.app.schemas as s\n"
        "from pydantic import BaseModel\n"
        "out = {}\n"
        "for n in dir(s):\n"
        "    o = getattr(s, n)\n"
        "    if isinstance(o, type) and issubclass(o, BaseModel):\n"
        "        mc = getattr(o, 'model_config', None) or {}\n"
        "        out[n] = bool(mc.get('from_attributes'))\n"
        "print('JSON' + json.dumps(out))\n"
    )
    r = _run(code)
    assert r.returncode == 0, r.stderr[-2000:]
    line = [x for x in r.stdout.splitlines() if x.startswith("JSON")][0]
    flags = json.loads(line[4:])

    assert len(flags) > 100, f"only found {len(flags)} schemas"
    with_attr = [k for k, v in flags.items() if v]
    assert len(with_attr) == 34, (
        f"expected 34 schemas with from_attributes=True, got {len(with_attr)}: "
        f"{sorted(with_attr)[:10]}"
    )


def test_settings_env_file_config_survived_the_migration():
    """core/config.py moved to SettingsConfigDict; env loading must still work."""
    code = (
        "from backend.app.core.config import Settings\n"
        "s = Settings(_env_file=None)\n"
        "print('CASE', s.APP_NAME)\n"
        "print('DEBUG', s.DEBUG)\n"
        "print('CORS', len(s.cors_origin_list))\n"
    )
    r = _run(code)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "CASE Shresth Masala Industry Management" in r.stdout
    assert "DEBUG False" in r.stdout, "DEBUG must still default to False"
    assert "CORS 12" in r.stdout, "CORS origin list must still parse"


def test_clean_shutdown_does_not_leak_the_sqlite_handle():
    """The actual fix: serve the app, stop it, and exit with no warning."""
    code = (
        "import threading, time, warnings, uvicorn\n"
        "import backend.main as m\n"
        "cfg = uvicorn.Config(m.app, host='127.0.0.1', port=8123,"
        " log_level='error', lifespan='on')\n"
        "srv = uvicorn.Server(cfg)\n"
        "t = threading.Thread(target=srv.run, daemon=True); t.start()\n"
        "for _ in range(80):\n"
        "    if m._auto_backup_started: break\n"
        "    time.sleep(0.25)\n"
        "assert m._auto_backup_started, 'lifespan did not start'\n"
        "srv.should_exit = True; t.join(timeout=30)\n"
    )
    r = _run(code, timeout=300)
    assert r.returncode == 0, r.stderr[-3000:]
    assert "unclosed database" not in r.stderr, (
        "the sqlite connection is still leaking on shutdown:\n"
        + "\n".join(
            line for line in r.stderr.splitlines()
            if "unclosed" in line or "ResourceWarning" in line
        )
    )


def test_engine_dispose_is_wired_into_the_lifespan_shutdown():
    """Guards against the dispose call being dropped from the lifespan."""
    src = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    body = src.split("async def _lifespan(")[1].split("\napp = FastAPI")[0]
    assert "engine.dispose()" in body, (
        "the lifespan shutdown no longer disposes the engine, so the "
        "unclosed-database warning will come back"
    )
    assert "finally:" in body, "dispose() must be in a finally to run on errors"


def test_subprocess_boots_against_a_temp_database_not_live():
    """These boots must never touch data/masala_factory.db.

    The sqlite-handle test boots the whole app in a subprocess, whose
    scheduler tick writes an auto-backup row wherever the engine points.
    Pointed at the live file, that write trips the no-real-db-writes
    tripwire (same size, bumped mtime) depending on scheduler timing.
    """
    r = _run(
        "from backend.app.core.config import settings\n"
        "print('DBURL ' + settings.DATABASE_URL)"
    )
    assert r.returncode == 0, r.stderr[-2000:]
    line = [x for x in r.stdout.splitlines() if x.startswith("DBURL")][0]
    assert "masala_factory.db" not in line, (
        f"subprocess boots against the live database: {line}"
    )


def test_backup_files_resolve_inside_the_throwaway_dir_not_the_repo():
    """A backup attempt inside a subprocess boot must not land in data/.

    The default backup dir is the relative path data/backups, so a boot
    whose working directory is the repo root writes auto_*.db files into
    the real folder - every full-suite run littered it with dozens. The
    file lands before any database write, so the tmp DATABASE_URL alone
    does not stop it; only an isolated working directory does.
    """
    backups = ROOT / "data" / "backups"
    before = (
        sorted(p.name for p in backups.glob("*")) if backups.exists() else []
    )
    code = (
        "import backend.main as m\n"
        "from sqlalchemy import create_engine\n"
        "from sqlalchemy.orm import sessionmaker\n"
        "eng = create_engine('sqlite:///:memory:')\n"
        "db = sessionmaker(bind=eng)()\n"
        "try:\n"
        "    m._do_backup(db, is_auto=True, description='cwd probe')\n"
        "except Exception as e:\n"
        "    print('BACKUP-ATTEMPTED', type(e).__name__)\n"
    )
    r = _run(code)
    assert r.returncode == 0, r.stderr[-3000:]
    after = sorted(p.name for p in backups.glob("*")) if backups.exists() else []
    assert before == after, (
        f"subprocess backup wrote into the repo: {set(after) - set(before)}"
    )
