"""dist/ extras: launcher bat + help travel with the built app.

dist/ is wiped on every rebuild and only data/ is restored, so anything
placed there by hand vanishes. build.py therefore copies run-server.bat
(which starts the exe when source is absent) and HELP.txt next to the
executable after every build.

Run: python -m pytest backend/tests/test_dist_extras.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _read(relpath):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, *relpath.split("/")), encoding="utf-8") as fh:
        return fh.read()


def test_launcher_starts_exe_when_source_absent():
    bat = _read("run-server.bat")
    assert "ShresthMasalaIndustry.exe" in bat, "bat cannot start the built app"
    assert "http://127.0.0.1:8000/" in bat, "exe mode never opens the browser"


def test_browser_only_launcher():
    bat = _read("run-browser.bat")
    assert "http://127.0.0.1" in bat, "browser never opens"
    assert "uvicorn" not in bat, "starts a server instead of just opening"
    assert "ShresthMasalaIndustry.exe" not in bat, "starts the app window"
    assert "main_window" not in bat, "starts the app window"


def test_app_launcher_opens_window_without_console():
    bat = _read("run-app.bat")
    assert "main_window.py" in bat, "launcher does not open the app window"
    assert "pythonw" in bat, "console window will stay visible"


def test_build_ships_bat_and_help():
    build = _read("scripts/build.py")
    assert "run-server.bat" in build, "rebuild drops the launcher"
    assert "run-app.bat" in build, "rebuild drops the app launcher"
    assert "HELP.txt" in build, "rebuild drops the help file"


def test_help_covers_both_ways_to_run():
    help_txt = _read("HELP.txt")
    assert "ShresthMasalaIndustry.exe" in help_txt, "no exe instructions"
    assert "run-server.bat" in help_txt, "no dev-server instructions"
    assert "masala_factory.db" in help_txt, "data location undocumented"
