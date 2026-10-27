#!/usr/bin/env python
"""Build a clean copy of the app for a new system (masters only, no history).

Usage: python scripts/build_new_system_copy.py
Output: release_new_system/
  - ShresthMasalaIndustry/  (portable exe folder, clean data/)
  - ShresthMasalaIndustry-source-clean.zip  (source + clean DB)

Never touches the live DB: all cleaning happens on a temp copy.
"""
import shutil
import sqlite3
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The packaged app's database (what the user actually runs) lives next to
# the exe; the repo data/ copy is only used for source/dev runs and goes
# stale. Prefer the former when it exists.
_DIST_DB = ROOT / "dist" / "ShresthMasalaIndustry" / "data" / "masala_factory.db"
_REPO_DB = ROOT / "data" / "masala_factory.db"
LIVE_DB = _DIST_DB if _DIST_DB.exists() else _REPO_DB
OUT = ROOT / "release_new_system"

# Transactional / historical tables: wiped. Everything else (masters,
# settings, users, numbering) is kept as current data.
WIPE = [
    "audit_logs", "notification_logs", "backup_records",
    "attendance", "leaves", "deductions", "employee_advances",
    "salary_payments", "production_entries", "production_outputs",
    "production_stage_entries", "production_wastage",
    "purchase_invoice_items", "purchase_invoices",
    "sales_challan_items", "sales_challans",
    "sales_invoice_items", "sales_invoices",
    "customer_payments", "supplier_payments", "expenses",
    "cash_movements", "attachments", "saved_reports",
    "dashboard_shares", "dashboard_preferences",
    "finished_product_stock_movements", "raw_material_stock_movements",
    "size_rate_history", "import_reports",
]


def clean_db_copy(dest: Path) -> None:
    shutil.copy2(LIVE_DB, dest)
    con = sqlite3.connect(dest)
    try:
        cur = con.cursor()
        cur.execute("PRAGMA foreign_keys = OFF")
        existing = {r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for t in WIPE:
            if t in existing:
                cur.execute(f'DELETE FROM "{t}"')
        if "sqlite_sequence" in existing:
            placeholders = ",".join("?" * len(WIPE))
            cur.execute(
                f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})",
                WIPE,
            )
        con.commit()
        cur.execute("PRAGMA foreign_keys = ON")
        bad = list(cur.execute("PRAGMA foreign_key_check"))
        assert not bad, f"FK violations after wipe: {bad[:5]}"
        counts = {t: cur.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                  for t in WIPE if t in existing}
        assert all(v == 0 for v in counts.values()), counts
        masters = {}
        for t in ("customers", "suppliers", "raw_materials", "products",
                  "employees", "users"):
            if t in existing:
                masters[t] = cur.execute(
                    f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        print("masters kept:", masters)
        con.execute("VACUUM")
        con.commit()
    finally:
        con.close()


def build_portable(clean_db: Path) -> Path:
    src = ROOT / "dist" / "ShresthMasalaIndustry"
    if not (src / "ShresthMasalaIndustry.exe").exists():
        sys.exit("dist build missing: run python scripts/build.py first "
                 "(close the running app first).")
    target = OUT / "ShresthMasalaIndustry"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(src, target, ignore=shutil.ignore_patterns("app.log"))
    data = target / "data"
    if data.exists():
        shutil.rmtree(data)
    (data / "backups").mkdir(parents=True)
    shutil.copy2(clean_db, data / "masala_factory.db")
    # No secret shipped: new machine generates its own on first run.
    for stale in ("masala_factory.db.empty-fresh-20260929.bak",
                  "masala_factory.secret_key"):
        p = data / stale
        if p.exists():
            p.unlink()
    (target / "app.log").unlink(missing_ok=True)
    return target


def build_source_zip(clean_db: Path) -> Path:
    zip_path = OUT / "ShresthMasalaIndustry-source-clean.zip"
    skip_dirs = {"dist", "build", ".git", "__pycache__", ".pytest_cache",
                 ".venv", "venv", "release_new_system", "build-release",
                 "build-release-spec", "release", ".opencode",
                 ".cleanup-backup-20261008-125408",
                 ".footer-restore-backup-20261008-235942",
                 ".build-data-stash", "graphify-out"}
    skip_files = {".env", "app.log", ".secret_key"}
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in ROOT.rglob("*"):
            rel = p.relative_to(ROOT)
            if any(part in skip_dirs or part.endswith(".egg-info")
                   for part in rel.parts):
                continue
            if p.is_dir():
                continue
            if p.name in skip_files or p.suffix in (".db", ".db-wal",
                                                    ".db-shm", ".db-journal",
                                                    ".secret_key"):
                continue
            if p.suffix == ".pyc" or rel == zip_path.relative_to(ROOT):
                continue
            if "data/backups" in rel.as_posix():
                continue
            z.write(p, rel)
        z.write(clean_db, "data/masala_factory.db")
    return zip_path


def main() -> None:
    if not LIVE_DB.exists():
        sys.exit(f"live DB not found: {LIVE_DB}")
    print(f"source DB: {LIVE_DB}")
    OUT.mkdir(exist_ok=True)
    clean_db = OUT / "masala_factory.clean.db"
    work = OUT / "_work_clean.db"
    for stale in (clean_db, work):
        stale.unlink(missing_ok=True)
    clean_db_copy(dest=work)
    work.rename(clean_db)
    print(f"clean DB: {clean_db} ({clean_db.stat().st_size} bytes)")
    portable = build_portable(clean_db)
    print(f"portable: {portable}")
    zip_path = build_source_zip(clean_db)
    print(f"source zip: {zip_path} ({zip_path.stat().st_size} bytes)")
    clean_db.unlink()
    print("live DB untouched:", LIVE_DB.stat().st_size, "bytes")
    print("done. Copy release_new_system/ to the new PC via USB.")


if __name__ == "__main__":
    main()
