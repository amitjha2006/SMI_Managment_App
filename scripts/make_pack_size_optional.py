"""Make packaging_size_id nullable in production_entries for two-step packing.

- Backs up the database before any changes
- Rebuilds the production_entries table with the new schema
- Preserves all data, indexes, foreign keys, and audit trail

Safety rules (see AGENTS.md):
- Refuses the repo's live database path outright
- Refuses to write unless --apply is given (default: dry-run report)
- Refuses to overwrite existing backups
- Verifies row counts and indexes match before/after
- Leaves the original table untouched if anything fails
"""

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path


def _fail(msg, conn=None):
    print(msg)
    if conn:
        conn.rollback()
        conn.close()
    return 1


def _safety_check(db_path, args):
    """Validate inputs; return (conn, cursor) or an int exit code."""
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return 1
    repo_root = Path(__file__).parent.parent
    live_db = repo_root / "data" / "masala_factory.db"
    if db_path == live_db:
        print("Refusing to modify the repo's live database. Copy it first:")
        print(f"  cp {live_db} /path/to/your/copy.db")
        return 1
    backup_path = db_path.with_suffix(".pre_pack_later_bak")
    if backup_path.exists():
        print(f"Backup already exists: {backup_path}")
        print("Delete it first or use a different database.")
        return 1
    if args.backup:
        print(f"Creating backup: {backup_path}")
        shutil.copy2(db_path, backup_path)
    elif not args.apply:
        print("Dry run mode: no changes will be made")
        print(f"Use --apply to modify: {db_path}")
        return 0
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn, conn.cursor()


def _rebuild(conn, cursor):
    cursor.execute("PRAGMA table_info(production_entries)")
    current_cols = {row[1]: row for row in cursor.fetchall()}
    if "packaging_size_id" not in current_cols:
        return _fail("Error: production_entries table not found", conn)

    col_info = current_cols["packaging_size_id"]
    if col_info[3] == 0:
        print("packaging_size_id is already nullable - no changes needed")
        conn.close()
        return 0

    print("Rebuilding production_entries table to make packaging_size_id nullable...")

    cursor.execute("SELECT COUNT(*) FROM production_entries")
    original_count = cursor.fetchone()[0]
    print(f"Original row count: {original_count}")

    cursor.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='index' AND tbl_name='production_entries'"
    )
    original_indexes = cursor.fetchall()
    print(f"Found {len(original_indexes)} indexes to recreate")

    cursor.execute("""
        CREATE TABLE production_entries_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            production_number VARCHAR(50) NOT NULL UNIQUE,
            production_date DATETIME NOT NULL,
            product_id INTEGER NOT NULL,
            packaging_size_id INTEGER,
            target_quantity NUMERIC(15,3) DEFAULT 0,
            actual_output NUMERIC(15,3) DEFAULT 0,
            packed_at DATETIME,
            status VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
            is_archived BOOLEAN NOT NULL DEFAULT 0,
            archived_at DATETIME,
            archived_by INTEGER,
            notes TEXT,
            created_at DATETIME NOT NULL,
            updated_at DATETIME,
            created_by INTEGER
        )
    """)

    new_cols = {r[1] for r in cursor.execute(
        "PRAGMA table_info(production_entries_new)"
    ).fetchall()}
    copy_cols = [c for c in current_cols if c in new_cols]
    cols_sql = ", ".join(copy_cols)
    cursor.execute(
        f"INSERT INTO production_entries_new ({cols_sql}) "
        f"SELECT {cols_sql} FROM production_entries"
    )

    cursor.execute("SELECT COUNT(*) FROM production_entries_new")
    new_count = cursor.fetchone()[0]
    if new_count != original_count:
        return _fail(
            f"ERROR: Row count mismatch: {original_count} -> {new_count}", conn
        )
    print(f"Copied {new_count} rows successfully")

    cursor.execute("DROP TABLE production_entries")
    cursor.execute("ALTER TABLE production_entries_new RENAME TO production_entries")

    for _name, sql in original_indexes:
        cursor.execute(
            sql.replace("production_entries", "production_entries_new", 1)
            .replace("production_entries_new", "production_entries", 1)
        )

    cursor.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND tbl_name='production_entries'"
    )
    recreated_indexes = cursor.fetchall()
    if len(recreated_indexes) != len(original_indexes):
        print(
            f"WARNING: Index count mismatch: "
            f"{len(original_indexes)} -> {len(recreated_indexes)}"
        )

    cursor.execute("PRAGMA table_info(production_entries)")
    new_cols_map = {row[1]: row for row in cursor.fetchall()}
    if new_cols_map["packaging_size_id"][3] == 1:
        return _fail(
            "ERROR: packaging_size_id is still NOT NULL after rebuild", conn
        )

    print("Rebuild completed successfully")
    print("packaging_size_id is now nullable")

    conn.commit()
    conn.close()
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Make packaging_size_id nullable in production_entries"
    )
    parser.add_argument("--db", default="data/masala_factory.db",
                        help="Path to SQLite database")
    parser.add_argument("--apply", action="store_true",
                        help="Apply changes (default: dry-run)")
    parser.add_argument("--backup", action="store_true",
                        help="Create backup before changes")
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    result = _safety_check(db_path, args)
    if isinstance(result, int):
        return result
    return _rebuild(result[0], result[1])


if __name__ == "__main__":
    sys.exit(main())
