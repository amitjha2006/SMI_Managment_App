#!/usr/bin/env python
"""
Windows .exe packaging script for the Shresth Masala Industry Management System.
Usage: python scripts/build.py
Output: dist/ShresthMasalaIndustry/ShresthMasalaIndustry.exe
(onedir; data/ created next to it at runtime)

Notes:
- backend/ is picked up automatically as an imported package (do NOT --add-data it).
- data/ is deliberately NOT bundled: the live database must never ship inside
  the exe. It is created fresh next to the executable on first run.
- Only frontend/ static files are bundled as data.
"""

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

# Application/binary name. Renamed from SMIMasalaFactory; the old folder is
# migrated once (see main()) so a rebuild never strands live user data.
APP_NAME = "ShresthMasalaIndustry"
OLD_APP_DIR_NAMES = ("SMIMasalaFactory",)


def ensure_icon(root: Path) -> str | None:
    """PyInstaller needs .ico on Windows; build it from the company logo.

    Prefer the full-resolution master: logo.png is a display-sized copy (192px)
    and cannot supply the 256px frame, so building from it silently drops that
    size from the packaged app's icon.
    """
    assets = root / "frontend" / "assets"
    ico = assets / "icon.ico"
    master = assets / "logo-original-1254.png"
    logo = assets / "logo.png"
    src = master if master.exists() else logo
    if src.exists() and (not ico.exists() or src.stat().st_mtime > ico.stat().st_mtime):
        try:
            from PIL import Image  # noqa: PLC0415 - fail soft without Pillow

            img = Image.open(src).convert("RGBA")
            img.save(
                ico, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (128, 128), (256, 256)]
            )
            print(f"Built {ico} from {src.name}")
        except ImportError:
            print("Pillow not available, cannot build icon.")
        except Exception as exc:
            print(f"Icon build failed ({exc}), continuing without it.")
            return str(ico) if ico.exists() else None
    return str(ico) if ico.exists() else None


# Modules PyInstaller collects transitively but the app never uses at runtime.
# Proven safe by running the full test suite with each made unimportable:
# - numpy: via openpyxl.compat.numbers (guarded `try: import numpy`)
# - setuptools/pkg_resources: via passlib.pwd (asset loading never called)
# - scipy/pandas/matplotlib/tkinter: not dependencies at all, belt and braces
# NOTE: PIL must NOT be excluded. reportlab.lib.utils does
# `from PIL import Image` unconditionally at import time, so excluding PIL
# makes every PDF export crash in the frozen exe with
# ModuleNotFoundError: No module named 'PIL' (seen in dist app.log).
EXCLUDES = [
    "numpy",
    "setuptools",
    "pkg_resources",
    "scipy",
    "pandas",
    "matplotlib",
    "tkinter",
]


def ship_extras(root: Path, dist_path: Path) -> None:
    """Copy the launcher + help next to the executable.

    dist/ is wiped at the top of main(), so hand-placed files vanish;
    shipping them here makes every build carry them.
    """
    target = dist_path / APP_NAME
    if not target.is_dir():
        return
    for extra in ("run-server.bat", "run-app.bat", "run-browser.bat", "HELP.txt"):
        origin = root / extra
        if origin.is_file():
            shutil.copy2(origin, target / extra)
            print(f"Shipped {extra} next to the executable")


def update_cache_buster(root: Path, dist_path: Path) -> None:
    """Hash components.js and update the cache-buster in index.html.

    The frontend references components.js?v=N in index.html. This function
    computes a short hash of the bundled components.js and rewrites the
    version parameter so browsers fetch the new file on deploy.
    """
    import re
    frontend_dir = dist_path / APP_NAME / "_internal" / "frontend"
    components_js = frontend_dir / "js" / "components.js"
    index_html = frontend_dir / "index.html"

    if not components_js.exists() or not index_html.exists():
        print("Cache-buster: components.js or index.html not found in build output")
        return

    # Compute hash of components.js
    content = components_js.read_bytes()
    file_hash = hashlib.sha256(content).hexdigest()[:8]

    # Update index.html
    html = index_html.read_text(encoding="utf-8")
    # Match: components.js?v=N or components.js?v=14 etc.
    new_html = re.sub(
        r'(components\.js\?v=)[^"\']+',
        rf'\g<1>{file_hash}',
        html
    )
    if new_html != html:
        index_html.write_text(new_html, encoding="utf-8")
        print(f"Updated cache-buster: components.js?v={file_hash}")
    else:
        print("Cache-buster: no change needed")


def clean_previous(root: Path, dist_path: Path, build_path: Path) -> None:
    """Remove previous build outputs (dist/ holds live data — stash first)."""
    for p in [
        dist_path,
        build_path,
        root / f"{APP_NAME}.spec",
        root / "SMIMasalaFactory.spec",
        root / "MasalaFactory.spec",
    ]:
        if p.exists():
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()


def main():
    root = Path(__file__).resolve().parent.parent
    dist_path = root / "dist"
    build_path = root / "build"

    # Preserve the live database across rebuilds: dist/ holds the previous
    # run's data/ (database + backups) next to the executable, and wiping it
    # would silently destroy every user account and all business data
    # (which is exactly what happened once before this guard existed).
    live_data = dist_path / APP_NAME / "data"
    if not live_data.exists():
        # One-time rename migration: carry data forward from the old folder.
        for old in OLD_APP_DIR_NAMES:
            legacy = dist_path / old / "data"
            if legacy.exists():
                live_data = legacy
                print(f"Found data under previous app name at {legacy}")
                break
    data_stash = root / ".build-data-stash"
    if live_data.exists():
        if data_stash.exists():
            shutil.rmtree(data_stash)
        shutil.copytree(live_data, data_stash)
        print(f"Stashed live data from {live_data}")

    # Ensure clean previous builds
    clean_previous(root, dist_path, build_path)
    # Drop the previous-name output folder AFTER stashing (the restore below
    # targets the new name, so this never strands data).
    for old in OLD_APP_DIR_NAMES:
        legacy_out = dist_path / old
        if legacy_out.exists() and legacy_out.name != APP_NAME:
            shutil.rmtree(legacy_out)
            print(f"Removed previous app folder {legacy_out}")

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
        "frontend;frontend",
        "--add-data",
        "VERSION;.",
        "--hidden-import",
        "fastapi",
        "--hidden-import",
        "uvicorn",
        "--hidden-import",
        "sqlalchemy",
        "--hidden-import",
        "pydantic",
        "--hidden-import",
        "pydantic_settings",
        "--hidden-import",
        "webview",
        "--hidden-import",
        "openpyxl",
        "--hidden-import",
        "reportlab",
        "--hidden-import",
        "PIL",
        "--hidden-import",
        "jose",
        "--hidden-import",
        "passlib",
        "--hidden-import",
        "passlib.handlers.pbkdf2",
        "--hidden-import",
        "multipart",
        "--hidden-import",
        "uharfbuzz",
        "--distpath",
        str(dist_path),
        "--workpath",
        str(build_path),
        "--specpath",
        str(root),
        "main_window.py",
    ]
    for mod in EXCLUDES:
        cmd.extend(["--exclude-module", mod])
    icon = ensure_icon(root)
    if icon:
        cmd.extend(["--icon", icon])

    print("Starting build...")
    print(" ".join(cmd))
    # check=False: the return code is inspected below so a failed build
    # prints its own guidance instead of raising here.
    result = subprocess.run(cmd, cwd=str(root), check=False)

    # Put the live database back next to the fresh executable. A rebuild
    # must never ship (or leave behind) an empty database — and on failure
    # the stash is still restored so no data is ever stranded by a bad build.
    if data_stash.exists():
        target = dist_path / APP_NAME / "data"
        if target.exists():
            shutil.rmtree(target)
        if dist_path.exists():
            shutil.copytree(data_stash, target)
            print(f"Restored live data to {target}")
        else:
            print(f"Build output missing; live data kept safe at {data_stash}")
            sys.exit(result.returncode if result.returncode else 1)
        shutil.rmtree(data_stash)

    # dist/ is wiped above, so the launcher + help travel with the build
    # instead of being placed by hand (hand-placed files vanish here).
    if result.returncode == 0:
        ship_extras(root, dist_path)
        update_cache_buster(root, dist_path)
        # Bundle VERSION next to the exe so /system/info can report the version
        try:
            shutil.copy2(root / "VERSION", dist_path / APP_NAME / "VERSION")
            print("Bundled VERSION next to the executable")
        except Exception as exc:
            print(f"Could not bundle VERSION: {exc}")

    if result.returncode == 0:
        print(f"\nBuild complete: {dist_path / APP_NAME / f'{APP_NAME}.exe'}")
    else:
        print("\nBuild failed. Check output above.")
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
