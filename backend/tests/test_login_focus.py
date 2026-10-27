"""The sign-in screen must focus the username box on its own.

Typing your username after launch (or after Sign Out) used to need a click
first: nothing moved the caret. showLogin() is the one choke point both paths
go through, so the focus lives there.

Run: python -m pytest backend/tests/test_login_focus.py -v
"""

import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(*parts):
    with open(os.path.join(_ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def _fn(src, name):
    n = re.escape(name)
    m = re.search(
        r"(?:function\s+" + n + r"\s*\([^)]*\)\s*\{"
        r"|(?:window\.)?" + n + r"\s*=\s*(?:async\s+)?function\s*\([^)]*\)\s*\{)",
        src,
    )
    assert m, f"{name}() moved"
    depth, i = 1, m.end()
    while i < len(src) and depth:
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        i += 1
    return src[m.end():i]


def test_login_screen_focuses_the_username_box():
    body = _fn(_read("frontend", "js", "auth.js"), "showLogin")
    assert 'getElementById("login-username")' in body, (
        "showLogin no longer targets the username input"
    )
    assert ".focus()" in body, "the username box is not focused - typing needs a click first"
    # Deferred: the screen was display:none a tick ago and WebView2 drops an
    # immediate focus() on a not-yet-laid-out element.
    assert "setTimeout(" in body, "focus must be deferred one tick after showing the screen"


def test_switching_back_to_sign_in_focuses_too():
    body = _fn(_read("frontend", "js", "auth.js"), "switchTab")
    assert 'getElementById("login-username")' in body and ".focus()" in body, (
        "returning from the Register tab leaves the caret nowhere"
    )


def test_username_input_still_exists():
    html = _read("frontend", "index.html")
    assert re.search(r'<input[^>]*id="login-username"', html), (
        "index.html no longer has #login-username - the focus call would be a no-op"
    )
