#!/usr/bin/env python
"""
Shresth Masala Industry Desktop Application ? PyWebView Entry Point
Starts the FastAPI backend in a background thread, then opens the UI in a native desktop window.
Usage: python main_window.py
"""

import base64
import ctypes
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# When frozen (PyInstaller .exe): run with the .exe folder as working dir so
# relative paths (data/, backups/) resolve next to the executable.
if getattr(sys, "frozen", False):
    os.chdir(Path(sys.executable).parent)
    # Quieter logs in the packaged app
    os.environ.setdefault("DEBUG", "false")
    # webview.start(debug=True) turns on the right-click menu this app needs;
    # without PYWEBVIEW_LOG it would also dump pywebview's own INFO noise into
    # app.log on every launch.
    os.environ.setdefault("PYWEBVIEW_LOG", "warning")
    # Windowed apps have no console: redirect stray output to a log file so
    # a broken pipe can never silently kill the server thread. Try next to
    # the exe first; on some machines (read-only Program Files, etc.) fall
    # back to a user-writable AppData location so diagnostics are never lost.
    _log_path = None
    for candidate in (
        Path(sys.executable).parent / "app.log",
        Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ShresthMasala" / "app.log",
    ):
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            _log = candidate.open("a", buffering=1, encoding="utf-8", errors="replace")
            _log_path = candidate
            break
        except Exception:
            continue
    if _log is not None:
        sys.stdout = sys.stderr = _log
    else:
        print("[startup] WARNING: could not open app.log anywhere", file=sys.stderr)

import uvicorn  # noqa: E402
import webview  # noqa: E402

# debug=True is what enables the right-click menu, but it also makes pywebview
# auto-open the Edge DevTools window on first load. Kill only that popup.
webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = False

# Import backend so init_db runs
from backend.main import app  # noqa: E402

# Candidate ports, tried in order. 8000 is first because that is what the
# frontend, CORS and any bookmarks already expect.
_PREFERRED_PORTS = (8000, 8001, 8002, 8003, 8004, 8005)
_HOST = "127.0.0.1"
_MAX_PORT = 65535


def _enable_native_menus():
    """Turn WebView2's right-click menu on, then turn debug extras off.

    pywebview writes AreDefaultContextMenusEnabled = debug BEFORE the first
    page load, and WebView2 applies that only on the next navigation - so the
    window launches with debug=True (pywebview itself writes the switch True
    in time) and this helper drops the debug extras we don't want (DevTools,
    browser hotkeys, status bar) and installs the menu trim. pywebview's init
    callback can land after our first flip, so we keep re-flipping idempotently
    over the first seconds. MSHTML fallback: attribute absent, give up.
    """

    attached = []

    def _flip(native, hits):
        # Invoke is void, so success is reported back via `hits`.
        try:
            core = getattr(getattr(native, "webview", None), "CoreWebView2", None)
            if core is None:
                return
            s = core.Settings
            s.AreDefaultContextMenusEnabled = True
            # debug mode turned these on too; the desktop app wants none
            s.AreDevToolsEnabled = False
            s.AreBrowserAcceleratorKeysEnabled = False
            s.IsStatusBarEnabled = False
            if not attached:
                attached.append(True)
                core.ContextMenuRequested += _on_context_menu_requested
            hits.append(True)
        except Exception:
            pass

    for _ in range(240):  # ~60s of 0.25s tries
        win = webview.windows[0] if webview.windows else None
        native = getattr(win, "native", None)
        if native is None:
            time.sleep(0.25)
            continue
        # CoreWebView2 may only be touched on the WinForms UI thread.
        hits: list = []
        try:
            from System import Action  # lazy: keeps the frozen exe fast

            native.Invoke(Action(lambda n=native, h=hits: _flip(n, h)))
        except Exception:
            pass
        if hits:
            for _ in range(3):  # beat pywebview's late init write
                time.sleep(0.5)
                native.Invoke(Action(lambda n=native: _flip(n, [])))
            print("[startup] WebView2 right-click menu enabled", flush=True)
            return
        time.sleep(0.25)
    print("[startup] WebView2 right-click menu not enabled", flush=True)


# Browser-chrome rows hidden from WebView2's context menu. Row names verified
# by dumping item.Name on right-click in a dev run.
_HIDDEN_MENU_ROWS = {
    "back",
    "forward",
    "reload",
    "print",
    "saveas",
    "moretools",
    "inspectelement",
}


def _on_context_menu_requested(_sender, args):
    """Trim WebView2's default context menu.

    Left Handled=False so WebView2 keeps rendering its own menu UI - that
    renderer is what draws the spelling suggestions. We just Remove() the
    browser-chrome rows (and orphaned dividers) from the pre-filled list;
    everything else renders as usual.
    """
    try:
        menu = args.MenuItems
        for item in list(menu):
            if (item.Name or "").lower() in _HIDDEN_MENU_ROWS:
                menu.Remove(item)

        def blank(i):
            return (i.Name or "").lower() == "other" and i.CommandId == -1

        # Drop dividers left leading/trailing/doubled by the removals above.
        items = list(menu)
        gone = [
            item
            for idx, item in enumerate(items)
            if blank(item)
            and (not idx
                 or idx + 1 >= len(items)
                 or blank(items[idx - 1])
                 or blank(items[idx + 1]))
        ]
        for item in gone:
            menu.Remove(item)
    except Exception:  # noqa: BLE001
        pass


def _port_is_free(port: int) -> bool:
    """True when nothing is already listening on the loopback port.

    Checked by binding rather than by connecting: a connect() probe cannot
    tell "nothing there" apart from "something there that refuses us", and
    the second case is exactly the one that must not be mistaken for free.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # No SO_REUSEADDR here on purpose. We want the bind to fail if
        # another process is holding the port, which is the answer we need.
        try:
            s.bind((_HOST, port))
        except OSError:
            return False
    return True


def _resolve_port(preferred: int) -> int:
    """Preferred port when free, else the first free candidate.

    A requested port that is taken (or out of range) is an explicit error,
    not a silent hop: the caller asked for that port, so landing elsewhere
    would serve a surprise (see _pick_port for why the window URL must be
    exact).
    """
    if preferred:
        if 1 <= preferred <= _MAX_PORT and _port_is_free(preferred):
            return preferred
        raise SystemExit(
            f"Port {preferred} is not usable. "
            "Close whatever is using it or pick another port."
        )
    return _pick_port()


def _pick_port() -> int:
    """First free candidate port.

    Previously the port was hardcoded to 8000 and the window URL was hardcoded
    to match. On a machine where 8000 was already taken, uvicorn failed inside
    the server thread and the window still opened http://127.0.0.1:8000/ -
    showing the user whatever unrelated service owned that port, or a blank
    error page. Both read as "the app is broken".
    """
    for port in _PREFERRED_PORTS:
        if _port_is_free(port):
            return port
    raise SystemExit(
        "Could not find a free port. Tried: "
        + ", ".join(str(p) for p in _PREFERRED_PORTS)
        + ". Close whatever is using them and start the app again."
    )


# Any HTTP status below a server error proves our own server is answering:
# the root route redirects or 404s on some setups, and both are fine.
_HTTP_SERVER_ERROR = 500


def _wait_until_serving(port: int, timeout: float = 30.0) -> bool:
    """Block until this app's own server answers on `port`.

    Replaces a blind time.sleep(2). Two reasons it has to be a real check:
    a slow machine (antivirus scanning a cold SQLite file, first-run schema
    creation) takes longer than two seconds, and the failure mode of not
    waiting is a window that renders an error and never recovers.
    """
    url = f"http://{_HOST}:{port}/"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                # Any HTTP answer means it is our server. 200 is not required:
                # the root route redirects or 404s on some setups and both
                # prove the socket is serving.
                if resp.status < _HTTP_SERVER_ERROR:
                    return True
        except urllib.error.HTTPError:
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    return False


def run_server(port: int, ready: threading.Event, failed: list) -> None:
    """Run the FastAPI server in a background thread.

    Records the startup error instead of letting it vanish: an exception in a
    daemon thread is printed to the log and then the process carries on with
    no server at all, which is how a broken install looks like a hung window.
    """
    try:
        uvicorn.run(app, host=_HOST, port=port, log_level="warning")
    except BaseException as exc:  # noqa: BLE001 - must reach the main thread
        failed.append(exc)
    finally:
        ready.set()


class DesktopApi:
    """pywebview JS bridge for file downloads.

    WebView2 ignores blob-URL anchor downloads, so window.UI.download()
    posts the fetched bytes here instead. The browser fallback in
    components.js stays untouched for dev-server use.
    """

    def save_file(self, filename, b64data):
        try:
            raw = base64.b64decode(b64data or "")
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"decode failed: {exc}"}
        try:
            win = webview.windows[0] if webview.windows else None
            if win is None:
                return {"ok": False, "error": "no window"}
            picked = win.create_file_dialog(
                webview.SAVE_DIALOG, save_filename=filename or "download"
            )
            if not picked:
                return {"ok": False, "cancelled": True}
            path = picked[0] if isinstance(picked, (list, tuple)) else picked
            with Path(path).open("wb") as fh:
                fh.write(raw)
            return {"ok": True, "path": str(path)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}


def _alert(title: str, message: str) -> None:
    """Show a native message box.

    The app is built with --windowed, so it has no console. Anything printed
    to stdout goes to app.log, which a user installing on a new machine will
    never open. Without this, every startup failure presents as "nothing
    happens when I double-click it".
    """
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
        except Exception:
            pass
        else:
            return
    print(f"{title}: {message}")


def _apply_staged_update() -> None:
    """Apply a previously staged update before opening the window.

    update-staged/extracted/ next to the exe holds a downloaded release
    (new exe plus _internal tree). Windows locks in-use files and AGENTS.md
    forbids swapping into a running _internal, so we only apply here at
    startup when the old process is gone.

    After swapping, launch the new exe instance and exit so the user lands
    on the new version with a single restart.
    """
    if not getattr(sys, "frozen", False):
        return  # staged updates only apply to the packaged exe

    exe_dir = Path(sys.executable).parent
    staging = exe_dir / "update-staged"
    extracted = staging / "extracted"

    if not extracted.is_dir():
        return

    # Locate the new exe inside the extracted tree
    new_exe = None
    for item in extracted.iterdir():
        if item.is_file() and item.suffix.lower() == ".exe":
            new_exe = item
            break
    new_internal = extracted / "_internal"
    if not new_exe or not new_internal.is_dir():
        shutil.rmtree(staging, ignore_errors=True)
        return

    current_exe = exe_dir / Path(sys.executable).name
    current_internal = exe_dir / "_internal"
    bak_exe = exe_dir / f"{Path(sys.executable).name}.old"
    bak_internal = exe_dir / "_internal.old"

    # Clear any old backups
    if bak_exe.exists():
        bak_exe.unlink()
    if bak_internal.exists():
        shutil.rmtree(bak_internal, ignore_errors=True)

    # Move current files aside (rename only changes the directory entry;
    # open file handles kept by this process remain valid)
    current_exe.rename(bak_exe)
    if current_internal.exists():
        current_internal.rename(bak_internal)

    # Move new files into place
    new_exe.rename(current_exe)
    new_internal.rename(current_internal)

    # Clean up staging and backups
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(bak_internal, ignore_errors=True)
    bak_exe.unlink(missing_ok=True)

    print("[startup] Staged update applied; relaunching new version", flush=True)

    # Relaunch the updated exe (detached) and exit this old process
    try:
        subprocess.Popen(
            [str(current_exe)],
            cwd=str(exe_dir),
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    except Exception:  # noqa: BLE001
        print("[startup] Failed to relaunch updated exe", flush=True)
    os._exit(0)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Shresth Masala Industry desktop app.")
    ap.add_argument(
        "--server-only",
        action="store_true",
        help="Serve the app over HTTP without opening the desktop window (browser use).",
    )
    ap.add_argument(
        "--port",
        type=int,
        default=0,
        help="Preferred port (default: first free of 8000-8005).",
    )
    args = ap.parse_args()
    try:
        port = _resolve_port(args.port)
    except SystemExit as exc:
        _alert("Shresth Masala Industry", str(exc))
        raise

    if args.server_only:
        print(
            f"[startup] serving on http://{_HOST}:{port}/ "
            "(no window; Ctrl+C to stop)",
            flush=True,
        )
        uvicorn.run(app, host=_HOST, port=port, log_level="warning")
        raise SystemExit(0)

    print(f"[startup] using {_HOST}:{port}")

    ready = threading.Event()
    failed: list = []
    server_thread = threading.Thread(
        target=run_server, args=(port, ready, failed), daemon=True
    )
    server_thread.start()

    if not _wait_until_serving(port):
        detail = f"Server failed to start: {failed[0]}" if failed else (
            f"Server did not start listening on {_HOST}:{port} within 30 seconds."
        )
        _alert(
            "Shresth Masala Industry - cannot start",
            detail
            + "\n\nCheck app.log in the application folder for details."
            + "\n\nIf the database is locked, close any other copy of the app first.",
        )
        raise SystemExit(1)

    # Test environment indicator
    app_env = os.environ.get("APP_ENV", "").lower()
    title = "Shresth Masala Industry — Management System"
    if app_env == "test":
        title = "[TEST] " + title

    # Apply any staged update before opening the window (silent auto-install)
    _apply_staged_update()

    try:
        webview.create_window(
            title=title,
            url=f"http://{_HOST}:{port}/",
            width=1280,
            height=800,
            resizable=True,
            maximized=True,
            min_size=(1024, 720),
            js_api=DesktopApi(),
        )
        webview.start(_enable_native_menus, debug=True)
    except Exception as exc:  # noqa: BLE001
        # The most likely cause on a brand new machine is a missing Microsoft
        # Edge WebView2 Runtime, which pywebview needs to render the window.
        # It is not installed by default on every Windows edition.
        _alert(
            "Shresth Masala Industry - cannot open the window",
            f"The application window could not be created:\n\n{exc}\n\n"
            "If this is the first time running on this computer, install the "
            "Microsoft Edge WebView2 Runtime (the 'Evergreen' installer) and "
            "try again.\n\n"
            "See app.log in the application folder for details.",
        )
        raise SystemExit(1) from exc
