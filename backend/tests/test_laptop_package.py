"""Laptop package builder: a clean, data-free copy of the app for a new PC.

The package must NEVER contain live business data (data/*.db, backups,
secrets) or machine junk (dist/, caches, backups of backups). It must
always contain the runnable app (backend/, frontend/, launcher bat).

Run: python -m pytest backend/tests/test_laptop_package.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def test_package_plan_includes_runnable_app():
    from scripts.make_laptop_package import INCLUDE_DIRS, INCLUDE_FILES

    for needed in ("backend", "frontend", "templates"):
        assert needed in INCLUDE_DIRS, f"{needed} missing from package"
    assert "run-server.bat" in INCLUDE_FILES, "launcher bat missing from package"
    assert "run-browser.bat" in INCLUDE_FILES, "browser launcher missing from package"
    assert "run-app.bat" in INCLUDE_FILES, "windowed launcher missing from package"


def test_package_plan_excludes_data_and_junk():
    from scripts.make_laptop_package import EXCLUDE_DIRS, EXCLUDE_SUFFIXES

    for banned in ("dist", "release_new_system", "build", ".opencode", "__pycache__"):
        assert banned in EXCLUDE_DIRS, f"{banned} could leak into package"
    assert ".db" in EXCLUDE_SUFFIXES, "live databases could leak into package"
    assert ".secret_key" in "".join(EXCLUDE_SUFFIXES), "secrets could leak"


def test_builder_produces_clean_package(tmp_path):
    from scripts.make_laptop_package import build_package

    src = tmp_path / "src"
    (src / "backend").mkdir(parents=True)
    (src / "backend" / "main.py").write_text("# app", encoding="utf-8")
    (src / "frontend").mkdir()
    (src / "data").mkdir()
    (src / "data" / "masala_factory.db").write_text("LIVE", encoding="utf-8")
    (src / "dist").mkdir()
    (src / "dist" / "app.exe").write_text("exe", encoding="utf-8")
    (src / "run-server.bat").write_text("bat", encoding="utf-8")

    dest = tmp_path / "pkg"
    report = build_package(str(src), str(dest))

    assert (dest / "backend" / "main.py").exists()
    assert (dest / "run-server.bat").exists()
    assert (dest / "data").is_dir(), "fresh PC needs an empty data/ folder"
    assert not (dest / "data" / "masala_factory.db").exists(), "LIVE DB LEAKED"
    assert not (dest / "dist").exists(), "dist LEAKED"
    assert report["db_files"] == 0 and report["secret_files"] == 0
