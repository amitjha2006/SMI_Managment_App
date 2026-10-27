#!/usr/bin/env python
"""Build a clean laptop package: the app, no live data, ready to copy.

Copies the runnable source tree into a fresh folder for a new PC, leaving
behind everything that must never travel: live databases, backups,
secrets, the frozen exe, caches and spare folders. The result starts with
an empty database on first launch.

Safety: after copying, the script scans the package for *.db and
*secret_key* files and FAILS loudly if any slipped through.

Usage:
    python scripts/make_laptop_package.py --dest <folder>   (must not exist)
"""

import argparse
import shutil
from pathlib import Path

# Folders copied wholesale (code + assets + docs).
INCLUDE_DIRS = ("backend", "frontend", "templates", "scripts", "docs")

# Single files copied at the package root.
INCLUDE_FILES = (
    "main_window.py",
    "requirements.txt",
    "pyproject.toml",
    "README.md",
    "AGENTS.md",
    "HELP.txt",
    "run-server.bat",
    "run-browser.bat",
    "run-app.bat",
    "run_dev.bat",
    "opencode.json",
    ".env.example",
    ".gitignore",
)

# Never copied, at any depth.
EXCLUDE_DIRS = frozenset({
    "dist", "build", "release_new_system", ".opencode", ".idea",
    "__pycache__", ".pytest_cache", ".ruff_cache",
    "backups", "data",
    ".cleanup-backup-20261008-125408", ".footer-restore-backup-20261008-235942",
})

# Never copied, at any depth (live data + secrets first).
EXCLUDE_SUFFIXES = (".db", ".db-journal", ".db-wal", ".db-shm", ".bak", ".secret_key")


def _wanted(path: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in path.parts):
        return False
    name = path.name
    return not name.endswith(EXCLUDE_SUFFIXES)


def build_package(src: str, dest: str) -> dict:
    src_path = Path(src)
    dest_path = Path(dest)
    if dest_path.exists():
        print(f"Refusing: destination already exists: {dest}")
        return {"ok": False}
    if not (src_path / "backend" / "main.py").exists():
        print(f"Source does not look like the app folder: {src}")
        return {"ok": False}

    for dirname in INCLUDE_DIRS:
        origin = src_path / dirname
        if not origin.is_dir():
            continue
        shutil.copytree(
            origin, dest_path / dirname,
            ignore=lambda d, names: [n for n in names if not _wanted(Path(d) / n)],
        )
    for filename in INCLUDE_FILES:
        origin = src_path / filename
        if origin.is_file() and _wanted(origin):
            shutil.copy2(origin, dest_path / filename)

    # Fresh PC starts empty: folders only, never files.
    (dest_path / "data" / "backups").mkdir(parents=True, exist_ok=True)

    db_files = list(dest_path.rglob("*.db"))
    secret_files = list(dest_path.rglob("*secret_key*"))
    report = {"ok": True, "db_files": len(db_files), "secret_files": len(secret_files)}
    if db_files or secret_files:
        print("SAFETY FAILURE - data leaked into package:")
        for p in db_files + secret_files:
            print(f"  {p}")
        report["ok"] = False
        return report
    print(f"Clean package ready: {dest_path}")
    print("Copy this folder to the new laptop, then follow docs/SETUP_NEW_LAPTOP.md.")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Clean laptop package.")
    ap.add_argument("--dest", required=True, help="New folder (must not exist)")
    ap.add_argument("--src", default=str(Path(__file__).resolve().parent.parent))
    args = ap.parse_args()
    return 0 if build_package(args.src, args.dest).get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
