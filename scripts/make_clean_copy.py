#!/usr/bin/env python
"""Make a clean copy of the packaged app: company profile + users only.

Copies a source install folder (exe + _internal + data) to a new folder,
then wipes every business table from the COPY's database. Kept: users,
roles, permissions, role_permissions, and application_settings rows whose
key starts with 'company.'. Everything else (masters, transactions,
employees, expenses, audit logs, sequences, prefs, reports, footers,
categories, cash movements, notifications, backups registry) is deleted,
and the file is vacuumed.

The copy gets a fresh signing secret (old .secret_key* files are removed)
and starts with an empty backups folder: old backups hold full copies of
today's data and must not travel with a clean copy.

Usage:
    python scripts/make_clean_copy.py --src <install> --dest <new-folder>

Safety: the source database is only ever READ (shutil.copytree). Refuses
to run when --dest already exists. Never point --src/--dest at the live
repo data/; this is for installed copies under dist/.
"""

import argparse
import shutil
import sqlite3
from pathlib import Path

# Fully preserved tables (access control + logins).
KEEP_TABLES = frozenset({"users", "roles", "permissions", "role_permissions"})

# application_settings is wiped except the company profile keys
# (company.name/address/gstin/phone/email/terms).
COMPANY_PREFIX = "company."


def _copy_install(src: Path, dest: Path) -> Path:
    """Copy the install folder, minus backups; drop stale secrets."""
    print(f"Copying {src} -> {dest} ...")
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("backups"))
    data = dest / "data"
    (data / "backups").mkdir(parents=True, exist_ok=True)
    for stale in list(data.glob("*secret_key*")) + list(data.glob("*.bak")):
        if stale.is_file():
            stale.unlink()
            print(f"Removed {stale.name} (regenerated on first launch)")
    return data / "masala_factory.db"


def _wipe_database(db_path: Path) -> dict:
    """Delete business data from the copy; return kept-row counts."""
    conn = sqlite3.connect(db_path)
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "select name from sqlite_master where type = 'table' "
                "and name not like 'sqlite_%' order by name"
            ).fetchall()
        ]
        conn.execute("PRAGMA foreign_keys = OFF")
        wiped = []
        for table in tables:
            if table in KEEP_TABLES:
                continue
            if table == "application_settings":
                conn.execute(
                    "delete from application_settings where key not like ?",
                    (COMPANY_PREFIX + "%",),
                )
                continue
            conn.execute(f'delete from "{table}"')
            wiped.append(table)
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")
        (ok,) = conn.execute("PRAGMA integrity_check").fetchone()
        if ok != "ok":
            raise RuntimeError(f"integrity_check FAILED: {ok}")
        conn.execute("VACUUM")
        conn.commit()

        kept = {}
        for table in sorted(KEEP_TABLES):
            (kept[table],) = conn.execute(f'select count(*) from "{table}"').fetchone()
        (kept["company_settings"],) = conn.execute(
            "select count(*) from application_settings where key like ?",
            (COMPANY_PREFIX + "%",),
        ).fetchone()
        for table in wiped:
            (left,) = conn.execute(f'select count(*) from "{table}"').fetchone()
            if left:
                raise RuntimeError(f"rows remaining in {table}: {left}")
        kept["wiped_tables"] = len(wiped)
        return kept
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Clean app copy.")
    ap.add_argument("--src", required=True, help="Installed app folder")
    ap.add_argument("--dest", required=True, help="New folder (must not exist)")
    args = ap.parse_args()

    src = Path(args.src)
    dest = Path(args.dest)
    if not (src / "data" / "masala_factory.db").exists():
        print(f"Source database not found under {src / 'data'}")
        return 1
    if dest.exists():
        print(f"Refusing: destination already exists: {dest}")
        return 1

    db_path = _copy_install(src, dest)
    kept = _wipe_database(db_path)
    print(f"Wiped {kept['wiped_tables']} tables.")
    for key in ("users", "roles", "permissions", "role_permissions"):
        print(f"  {key}: {kept[key]} rows kept")
    print(f"  company.* settings: {kept['company_settings']} rows kept")
    print("All wiped tables are empty. integrity_check ok.")
    print(f"Clean copy ready: {dest}")
    print("Users sign in with their existing passwords.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
