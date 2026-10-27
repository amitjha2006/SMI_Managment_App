"""The packaged window keeps its right-click (spelling suggestion) menu.

pywebview disables default context menus outside debug mode
(edgechromium.py: AreDefaultContextMenusEnabled = _state['debug']), which
killed the spelling-correction menu on the change-note fields. main_window
turns just that one switch back on via Form.Invoke on the UI thread.

Run: python -m pytest backend/tests/test_native_context_menu.py -v
"""

import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_window_re_enables_webview2_context_menu():
    with open(os.path.join(_ROOT, "main_window.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert "webview.start(_enable_native_menus, debug=True)" in src, (
        "window launches without the context-menu helper"
    )
    i = src.find("def _enable_native_menus")
    assert i >= 0, "_enable_native_menus() moved"
    block = src[i:i + 1800]
    assert "AreDefaultContextMenusEnabled = True" in block, (
        "helper no longer flips the context-menu switch"
    )
    assert "Invoke" in block, (
        "CoreWebView2 access must be marshalled to the UI thread"
    )
    # debug=True at start is what writes the switch True BEFORE the first
    # navigation (WebView2 applies it only after the next navigation - a flip
    # after load was once too late). Debug extras are switched back off.
    assert "webview.start(_enable_native_menus, debug=True)" in src, (
        "window must launch with debug=True or the menu never applies"
    )
    assert "AreDevToolsEnabled = False" in block, (
        "debug must not leave DevTools reachable"
    )
    assert "AreBrowserAcceleratorKeysEnabled = False" in block, (
        "debug must not leave browser hotkeys (Ctrl+R/F5) runnable"
    )


def test_context_menu_chrome_rows_are_trimmed():
    """Right-click menu hides Back/Refresh/Save as/Print/More tools/Inspect.

    The handler must NOT set Handled: WebView2 renders the menu itself and
    that renderer is what draws the spelling suggestions - taking over the UI
    would remove the corrections along with the browser chrome.
    """
    with open(os.path.join(_ROOT, "main_window.py"), encoding="utf-8") as fh:
        src = fh.read()
    i = src.find("def _on_context_menu_requested")
    assert i >= 0, "_on_context_menu_requested() moved"
    j = src.find("def _port_is_free", i)
    entire = src[:j]
    for row in ("back", "forward", "reload", "print", "saveas", "moretools",
                "inspectelement"):
        assert f'"{row}"' in entire, f"hidden row missing: {row}"
    block = src[i:j]
    assert "args.Handled" not in block, (
        "handler must defer the menu UI to WebView2 or spelling suggestions die"
    )
    assert "ContextMenuRequested" in src, "event subscription went missing"
