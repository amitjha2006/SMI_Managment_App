"""Stability regressions for shipping this build to another machine.

Each test here corresponds to a defect that made the packaged app unsafe or
unusable on a fresh Windows machine, found while auditing before a
distributable build. They are grouped by the failure they prevent.

Run: python -m pytest backend/tests/test_fresh_install_stability.py
"""

import re
import sys

import pytest

ROOT = r"C:\Shresth_Masala_Application"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _read(rel):
    with open(ROOT + "\\" + rel.replace("/", "\\"), encoding="utf-8") as fh:
        return fh.read()


def _mkuser(db, username, role):
    from backend.app.models.user import User

    u = User(username=username, hashed_password="x", role=role, is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


# --------------------------------------------------------------------------
# 1. Signing secret must not be a published constant
# --------------------------------------------------------------------------


def test_secret_key_is_not_the_published_default():
    from backend.app.core.config import settings

    assert settings.SECRET_KEY != "dev-secret-change-in-production", (
        "the JWT signing key is still the literal string committed to the "
        "repo; anyone with the source can mint an admin token"
    )
    assert len(settings.SECRET_KEY) >= 32, "signing key is too short to be random"


def test_secret_is_not_hardcoded_in_source():
    """The default must be generated, not typed into config.py."""
    src = _read("backend/app/core/config.py")
    # The known-bad literal may appear in this file's own comments, but never
    # as the value assigned to SECRET_KEY.
    for line in src.splitlines():
        if "SECRET_KEY:" in line and "=" in line:
            assert "dev-secret-change-in-production" not in line, (
                f"SECRET_KEY is still assigned a literal: {line.strip()}"
            )


def test_secret_persists_across_processes():
    """A key regenerated per launch would sign everyone out every restart."""
    from backend.app.core.config import _persisted_secret

    first = _persisted_secret()
    second = _persisted_secret()
    assert first == second, "the signing secret is not stable across calls"


def test_debug_is_off_by_default():
    from backend.app.core.config import Settings

    assert Settings.model_fields["DEBUG"].default is False, (
        "DEBUG must not default to True in a shipped build"
    )


# --------------------------------------------------------------------------
# 2. The server must not listen on the LAN
# --------------------------------------------------------------------------


def test_direct_run_binds_loopback_only():
    """0.0.0.0 would put an unauthenticated-API database on the network."""
    src = _read("backend/main.py")
    tail = src.split('if __name__ == "__main__":')[-1]
    assert 'host="0.0.0.0"' not in tail, (
        "backend/main.py __main__ still binds 0.0.0.0; the whole business "
        "database would be reachable from the LAN"
    )
    assert 'host="127.0.0.1"' in tail


def test_desktop_entry_binds_loopback_only():
    src = _read("main_window.py")
    assert 'host="0.0.0.0"' not in src


# --------------------------------------------------------------------------
# 3. Startup must not assume port 8000 is free, nor race the server
# --------------------------------------------------------------------------


def test_entry_point_picks_a_free_port():
    src = _read("main_window.py")
    assert "_pick_port" in src, "no free-port selection in the desktop entry point"
    # The window URL must be built from the chosen port, not hardcoded.
    assert 'url=f"http://{_HOST}:{port}/"' in src, (
        "the webview URL is still pinned to a fixed port; if 8000 is taken the "
        "window shows whatever unrelated service owns it"
    )


def _code_only(src: str) -> str:
    """Source with comments and docstrings removed.

    Assertions about "is this call still in the code" have to look at code
    only. A comment that explains why the call was removed quotes the call
    verbatim, which is exactly what a naive substring check trips over.
    """
    import io
    import tokenize

    out = []
    prev_type = tokenize.INDENT
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and prev_type in (
            tokenize.INDENT,
            tokenize.NEWLINE,
            tokenize.NL,
        ):
            # Module/class/function docstring.
            continue
        out.append(tok.string)
        if tok.type not in (tokenize.NL, tokenize.NEWLINE):
            prev_type = tok.type
    return " ".join(out)


def test_entry_point_waits_for_a_real_server():
    src = _read("main_window.py")
    assert "_wait_until_serving" in src, (
        "startup still uses a blind sleep, so a slow machine opens the window "
        "before the server can answer"
    )
    assert "time.sleep(2)" not in _code_only(src), (
        "the blind 2 second startup sleep is back in the startup path"
    )


def test_port_probe_distinguishes_taken_from_free():
    """A connect() probe cannot tell these apart; bind() can."""
    import socket

    import main_window

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((main_window._HOST, 0))
        s.listen(1)
        taken = s.getsockname()[1]
        assert main_window._port_is_free(taken) is False, (
            "a port with a listener was reported free, so the app would fight "
            "an existing service for it"
        )
    assert main_window._port_is_free(0) in (True, False)  # does not raise


# --------------------------------------------------------------------------
# 4. First run on a clean machine must produce a usable admin
# --------------------------------------------------------------------------


def test_first_registered_user_becomes_admin(db):
    """Regression: role came from the request body, which defaults to viewer.

    With no users the /auth/register bootstrap is the only way in, and
    promoting a user requires admin rights - so a first account that was not
    an admin locked the installer out of their own fresh install, permanently.
    """
    from backend.app.schemas import UserCreate
    from backend.main import register

    req = type("R", (), {"headers": {}, "client": None})()
    # Deliberately ask for the weakest role; the server must override it.
    created = register(
        UserCreate(username="first_owner", password="secret123", role="viewer"),
        req,
        db,
    )
    assert str(created.role.value) == "admin", (
        "the first account on a fresh install is not an admin; the installer "
        "cannot administer their own installation"
    )


def test_bootstrap_role_is_forced_not_merely_defaulted(db):
    """A client asking for admin on a fresh install gets admin; asking for
    viewer still gets admin. Later users are unaffected (route is closed)."""
    from backend.app.schemas import UserCreate
    from backend.main import register

    req = type("R", (), {"headers": {}, "client": None})()
    register(UserCreate(username="owner_a", password="secret123", role="admin"), req, db)
    # Second call would be blocked by middleware in the real app, but the
    # function itself must not keep promoting users to admin.
    second = register(
        UserCreate(username="owner_b", password="secret123", role="viewer"), req, db
    )
    assert str(second.role.value) == "viewer", (
        "register() promoted a later user to admin; the bootstrap override "
        "leaked past the first account"
    )


def test_register_still_rejects_duplicate_username(db):
    from fastapi import HTTPException

    from backend.app.schemas import UserCreate
    from backend.main import register

    _mkuser(db, "taken_name", "admin")
    req = type("R", (), {"headers": {}, "client": None})()
    with pytest.raises(HTTPException):
        register(UserCreate(username="taken_name", password="secret123"), req, db)


# --------------------------------------------------------------------------
# 5. No dev-machine assumptions in the shipped tree
# --------------------------------------------------------------------------


def test_no_absolute_dev_paths_in_shipped_entry_points():
    """A hardcoded C:\\... path breaks the moment the app runs elsewhere."""
    for rel in ("main_window.py", "backend/app/core/config.py",
                "backend/app/core/database.py"):
        src = _read(rel)
        for m in re.finditer(r'["\']([A-Za-z]:\\\\[^"\']*)["\']', src):
            pytest.fail(f"{rel} hardcodes an absolute path: {m.group(1)}")


def test_secrets_are_not_shipped_in_the_repo():
    """.gitignore must keep the live database and signing key out of version
    control and out of any future distributable."""
    ignore = _read(".gitignore")
    for entry in ("data/", "dist/", "build/", ".secret_key", "*.db"):
        assert entry in ignore, f".gitignore is missing {entry}"
