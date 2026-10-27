#!/usr/bin/env python
"""
Clean distributable build for a machine that is NOT this one.

Usage: python scripts/build_release.py [--zip]

Why this is separate from scripts/build.py
------------------------------------------
scripts/build.py deliberately preserves dist/ShresthMasalaIndustry/data/ across
rebuilds, because that folder holds the live business database for THIS
machine. Copying that dist folder to another computer would therefore hand
over every customer, challan, invoice and user account, plus the signing key
that mints their login tokens.

This script builds to release/ instead, shares the packaging decisions with
build.py so the two cannot drift, and then verifies that no data/, database or
secret key ended up in the output.
"""

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# Reuse the icon builder and the proven exclusion list from the dev build
# rather than restating them, so a fix to one applies to both.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build import APP_NAME, EXCLUDES, ensure_icon  # noqa: E402

# Anything matching these must not exist anywhere in the release output.
FORBIDDEN_NAMES = {"data", ".secret_key", "app.log", ".env"}
FORBIDDEN_SUFFIXES = {".db", ".db-wal", ".db-shm", ".db-journal"}


def _build(root: Path, out_dir: Path) -> int:
    work_dir = root / "build-release"
    spec_dir = root / "build-release-spec"

    for p in (out_dir, work_dir, spec_dir):
        if p.exists():
            shutil.rmtree(p)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name",
        APP_NAME,
        "--windowed",
        "--onedir",
        "--noconfirm",
        "--add-data",
        # Absolute: PyInstaller resolves a relative --add-data against the
        # spec file's folder, and this script keeps the spec out of the repo
        # root, so "frontend;frontend" would not be found.
        f"{root / 'frontend'};frontend",
        "--hidden-import", "fastapi",
        "--hidden-import", "uvicorn",
        "--hidden-import", "sqlalchemy",
        "--hidden-import", "pydantic",
        "--hidden-import", "pydantic_settings",
        "--hidden-import", "webview",
        "--hidden-import", "openpyxl",
        "--hidden-import", "reportlab",
        "--hidden-import", "PIL",
        "--hidden-import", "jose",
        "--hidden-import", "passlib",
        "--hidden-import", "passlib.handlers.pbkdf2",
        "--hidden-import", "multipart",
        "--distpath", str(out_dir),
        "--workpath", str(work_dir),
        "--specpath", str(spec_dir),
        "main_window.py",
    ]
    for mod in EXCLUDES:
        cmd.extend(["--exclude-module", mod])
    icon = ensure_icon(root)
    if icon:
        cmd.extend(["--icon", icon])

    print("Starting clean release build...")
    print(" ".join(cmd))
    return subprocess.run(cmd, cwd=str(root), check=False).returncode


def _verify_clean(out_dir: Path) -> list[str]:
    """Return a list of problems; empty means the output is safe to ship."""
    problems = []
    for p in out_dir.rglob("*"):
        if p.is_dir():
            if p.name in FORBIDDEN_NAMES:
                problems.append(f"directory shipped: {p.relative_to(out_dir)}")
        elif p.name in FORBIDDEN_NAMES or p.suffix in FORBIDDEN_SUFFIXES:
            problems.append(f"file shipped: {p.relative_to(out_dir)}")
    return problems


def _write_readme(out_dir: Path) -> None:
    app_dir = out_dir / APP_NAME
    (app_dir / "HOW-TO-INSTALL.txt").write_text(
        """Shresth Masala Industry - Management System
========================================
WHAT TO COPY
------------
Copy the WHOLE "ShresthMasalaIndustry" folder to the other computer.
The .exe cannot run on its own - it needs the "_internal" folder next to it.
Do not rename the exe out of its folder.

WHERE TO PUT IT
---------------
Put it in a normal, writable folder, for example:

    C:\\Shresth Masala

Do NOT put it in C:\\Program Files. The app creates its database next to the
.exe and Windows will not let a normal user write there.

FIRST RUN
---------
1. Double-click ShresthMasalaIndustry.exe
2. Click the "Register" tab
3. Create the first account
   IMPORTANT: the very first account is always an Administrator, whatever
   role is selected. That is how you get access on a new machine.
4. Later accounts are created from Settings > Users by an Administrator.

If a window does not open
-------------------------
This computer may not have the Microsoft Edge WebView2 Runtime, which the
application needs in order to draw its window. Install the "Evergreen"
installer from Microsoft and run it again.

If a message box explains what went wrong, the details are in app.log in this
same folder.

NETWORK / FIREWALL
------------------
The app only listens on 127.0.0.1 (this computer). No other computer on the
network can reach it, and it needs internet access for nothing. If Windows
asks about firewall permission, it is safe to decline.

BACKING UP
----------
The database lives in the "data" folder next to the .exe. Copy that whole
folder to back up everything. The app also makes automatic daily backups in
data\\backups.
""",
        encoding="utf-8",
    )
    print(f"Wrote install instructions to {app_dir / 'HOW-TO-INSTALL.txt'}")


def _make_zip(out_dir: Path) -> Path:
    app_dir = out_dir / APP_NAME
    zip_path = out_dir / f"{APP_NAME}.zip"
    if zip_path.exists():
        zip_path.unlink()
    print(f"Zipping {app_dir} -> {zip_path} ...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(app_dir.rglob("*")):
            z.write(p, p.relative_to(app_dir.parent))
    return zip_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", action="store_true", help="also produce a zip")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    out_dir = root / "release"

    rc = _build(root, out_dir)
    app_dir = out_dir / APP_NAME
    if rc != 0 or not (app_dir / f"{APP_NAME}.exe").exists():
        print("\nBuild failed. Check output above.")
        return rc or 1

    problems = _verify_clean(out_dir)
    if problems:
        print("\nBUILD REJECTED - the output would ship live data or secrets:")
        for p in problems:
            print(f"  - {p}")
        return 2

    _write_readme(out_dir)

    exe = app_dir / f"{APP_NAME}.exe"
    total = sum(f.stat().st_size for f in app_dir.rglob("*") if f.is_file())
    print("\n" + "=" * 60)
    print("Clean release build complete")
    print("=" * 60)
    print(f"  folder : {app_dir}")
    print(f"  exe    : {exe}")
    print(f"  size   : {total / (1024 * 1024):.1f} MB")
    print("  data/  : not present (created on first run)")
    print("  keys   : not present (generated on first run)")

    if args.zip:
        z = _make_zip(out_dir)
        print(f"  zip    : {z} ({z.stat().st_size / (1024 * 1024):.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
