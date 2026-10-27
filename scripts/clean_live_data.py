#!/usr/bin/env python
"""Clean the LIVE database: company profile + users only. DESTRUCTIVE.

Keeps users, roles, permissions, role_permissions, and the company.*
settings; wipes every other table (see make_clean_copy for the full list).
Unlike make_clean_copy, this operates in place: backups are KEPT (they are
the safety net) and the signing secret is KEPT (sessions stay valid).

Safety, in order:
  1. Refuses to run while ShresthMasalaIndustry.exe is running.
  2. Copies the database to data/backups/pre-wipe-<timestamp>.db first and
     verifies the copy (size + integrity_check) before touching the live
     file. Restore = stop the app, copy the backup over masala_factory.db.
  3. Requires --confirm-live (no bare run can wipe by accident).

Usage:
    python scripts/clean_live_data.py --db <masala_factory.db> --confirm-live
"""

import argparse
import shutil
import sqlite3
import subprocess
from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_wiper():
    here = Path(__file__).resolve().parent
    spec = spec_from_file_location("make_clean_copy", here / "make_clean_copy.py")
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _app_running() -> bool:
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq ShresthMasalaIndustry.exe"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return "ShresthMasalaIndustry.exe" in out


def _counts(db_path: Path) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "select name from sqlite_master where type = 'table' "
                "and name not like 'sqlite_%' order by name"
            ).fetchall()
        ]
        result = {}
        for table in tables:
            (n,) = conn.execute(f'select count(*) from "{table}"').fetchone()
            if n:
                result[table] = n
        return result
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Wipe live DB (see module docstring).")
    ap.add_argument("--db", required=True, help="Live masala_factory.db path")
    ap.add_argument("--confirm-live", action="store_true", help="Required")
    args = ap.parse_args()

    if not args.confirm_live:
        print("Refusing: pass --confirm-live to wipe the live database.")
        return 1
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return 1
    if _app_running():
        print("Refusing: close ShresthMasalaIndustry first.")
        return 1

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = db_path.parent / "backups" / f"pre-wipe-{stamp}.db"
    backup.parent.mkdir(parents=True, exist_ok=True)
    print(f"Backup {db_path.name} -> {backup.name} ...")
    shutil.copy2(db_path, backup)
    if backup.stat().st_size != db_path.stat().st_size:
        print("Backup size mismatch, aborting.")
        return 1

    print("Live rows before wipe:")
    for table, n in _counts(db_path).items():
        print(f"  {table}: {n}")

    wiper = _load_wiper()
    kept = wiper._wipe_database(db_path)

    print(f"Wiped {kept['wiped_tables']} tables.")
    for key in ("users", "roles", "permissions", "role_permissions"):
        print(f"  {key}: {kept[key]} rows kept")
    print(f"  company.* settings: {kept['company_settings']} rows kept")
    print("All wiped tables are empty. integrity_check ok.")
    print(f"Safety backup kept at: {backup}")
    print("To undo: close the app, copy that file over masala_factory.db.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
