"""Repair a SQLite database whose pages are corrupt ("database disk image is malformed").

Why: one unreadable table page makes every query that touches it raise
DatabaseError, so FastAPI answers 500 and the dashboard shows "—" on every KPI
card while the rest of the page still works (only that one endpoint reads the
damaged table). The file is fine for every other table, so rebuilding the
database from the rows that ARE readable keeps live data.

Default is a dry run: it reports what is unreadable and writes the repaired copy
to <db>.repaired for inspection, but never touches the original.
--apply swaps it in and refuses to run without --backup.

    python scripts/fix_corrupt_db.py --db <live.db> \
        [--fallback <older-clean.db>] [--salvage <rows.json>] [--apply --backup]

  --fallback  auto-backup taken before the damage; rows unreadable in the live
              file are restored from it (live rows always win).
  --salvage   JSON written by hand for rows that exist nowhere readable:
              [{"table": "...", "rows": [{col: value, ...}, ...]}, ...]
              Rows are inserted by column name, never by position - a table that
              grew by ALTER TABLE has a different on-disk field order than the
              rows still sitting on a dead page.

Example (app closed first):
    Get-Process ShresthMasalaIndustry*      # must print nothing
    python scripts/fix_corrupt_db.py \
        --db "C:/Shresth_Masala_Application_New/ShresthMasala-Laptop/data/masala_factory.db" \
        --fallback "<data/backups/auto_*.db>" --salvage salvage.json --apply --backup
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys

SKIP = "sqlite_%"
MAGIC = b"SQLite format 3\x00"


def connect(path: str) -> sqlite3.Connection:
    return sqlite3.connect(path, isolation_level=None)


def tables(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        f"SELECT name FROM main.sqlite_master WHERE type='table'"
        f" AND name NOT LIKE '{SKIP}' ORDER BY name")]


def columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f'PRAGMA main.table_info("{table}")')]


def integrity(conn: sqlite3.Connection):
    try:
        return conn.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.DatabaseError as exc:
        return [("ERROR", str(exc))]


def scan(conn: sqlite3.Connection) -> dict[str, tuple[list[int], list[int], bool]]:
    """table -> (readable rowids, unreadable rowids, damaged?).

    A damaged b-tree can still answer count(*) from an index while returning
    fewer rows from a table scan, so "clean" means both agree; only then is the
    table copied in one shot. Otherwise walk rowids and keep what still reads.
    """
    found = {}
    for t in tables(conn):
        try:
            total = conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        except sqlite3.DatabaseError:
            total = None
        try:
            rows = conn.execute(f'SELECT * FROM "{t}"').fetchall()
            clean = total is not None and len(rows) == total
        except sqlite3.DatabaseError:
            clean = False
        if clean:
            found[t] = ([], [], False)
            continue
        try:
            ids = [r[0] for r in conn.execute(f'SELECT rowid FROM "{t}"')]
        except sqlite3.DatabaseError:
            ids = []
        good, bad = [], list(ids)
        for rid in ids:
            try:
                if conn.execute(f'SELECT 1 FROM "{t}" WHERE rowid=?', (rid,)).fetchone():
                    good.append(rid)
                    bad.remove(rid)
            except sqlite3.DatabaseError:
                pass
        found[t] = (good, bad, True)
    return found


def build(src: str, out: str, fallback: str | None, salvage: dict | None) -> dict:
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(out + suffix):
            os.remove(out + suffix)

    source = connect(src)
    target = connect(out)
    target.execute("PRAGMA foreign_keys=OFF")
    for (stmt,) in source.execute(
            f"SELECT sql FROM main.sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE '{SKIP}'"):
        target.execute(stmt)

    state = scan(source)
    report = {"copied": {}, "from_fallback": {}, "salvaged": {}, "lost": {}}
    for t, (good, bad, hurt) in state.items():
        cols = columns(target, t)
        placeholders = ",".join("?" * len(cols))
        inserted = 0
        target.execute("BEGIN")
        if not hurt:
            rows = source.execute(f'SELECT * FROM "{t}"').fetchall()
            target.executemany(f'INSERT INTO main."{t}" VALUES ({placeholders})', rows)
            inserted = len(rows)
        else:
            for rid in good:
                row = source.execute(f'SELECT * FROM "{t}" WHERE rowid=?', (rid,)).fetchone()
                if row is not None:
                    target.execute(f'INSERT INTO main."{t}" VALUES ({placeholders})', row)
                    inserted += 1
        target.execute("COMMIT")
        report["copied"][t] = inserted
        if not bad:
            continue
        restored = 0
        if fallback and os.path.exists(fallback):
            fb = connect(fallback)
            for rid in bad:
                try:
                    row = fb.execute(f'SELECT * FROM "{t}" WHERE rowid=?', (rid,)).fetchone()
                except sqlite3.DatabaseError:
                    row = None
                if row is not None:
                    target.execute(f'INSERT OR IGNORE INTO main."{t}" VALUES ({placeholders})', row)
                    restored += 1
            fb.close()
        report["from_fallback"][t] = restored
        if len(bad) - restored:
            report["lost"][t] = len(bad) - restored

    for entry in (salvage or {}).get("tables", []):
        t = entry["table"]
        cols = columns(target, t)
        for record in entry.get("rows", []):
            names = [c for c in cols if c in record]
            if not names:
                continue
            cols_sql = ",".join(f'"{c}"' for c in names)
            target.execute('INSERT OR IGNORE INTO main."{}" ({}) VALUES ({})'
                           .format(t, cols_sql, ",".join("?" * len(names))),
                           [record[c] for c in names])
            report["salvaged"][t] = report["salvaged"].get(t, 0) + 1

    source.close()
    target.execute("VACUUM")
    result = {
        "integrity": integrity(target),
        "foreign_key_violations": target.execute("PRAGMA foreign_key_check").fetchall(),
        "report": report,
    }
    target.close()
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="database to repair")
    ap.add_argument("--fallback", help="older clean backup to restore unreadable rows from")
    ap.add_argument("--salvage", help="JSON of rows to re-insert by column name")
    ap.add_argument("--apply", action="store_true", help="replace the original (needs --backup)")
    ap.add_argument("--backup", action="store_true", help="keep the damaged file before --apply")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print("No such database:", args.db)
        return 2
    with open(args.db, "rb") as fh:
        if fh.read(16) != MAGIC:
            print("Not a SQLite database:", args.db)
            return 2
    if args.apply and not args.backup:
        print("--apply requires --backup: the damaged file is kept next to the repaired one.")
        return 2

    salvage = None
    if args.salvage:
        with open(args.salvage, encoding="utf-8") as fh:
            salvage = json.load(fh)

    conn = connect(args.db)
    before = integrity(conn)
    if before == [("ok",)]:
        print("Integrity check: ok - nothing to repair.")
        conn.close()
        return 0
    state = scan(conn)
    conn.close()
    print("Integrity check:", before[:3])
    damaged = {t: (len(v[1]) or "dead pages") for t, v in state.items() if v[2]}
    print("Damaged tables :", damaged or "none")

    out = args.db + ".repaired"
    result = build(args.db, out, args.fallback, salvage)
    print("Rebuilt        :", out)
    print("Rows copied    :", sum(result["report"]["copied"].values()))
    print("From fallback  :", result["report"]["from_fallback"] or "none")
    print("Salvaged       :", result["report"]["salvaged"] or "none")
    print("Unrecoverable  :", result["report"]["lost"] or "none")
    print("New integrity  :", result["integrity"][:3])
    if result["foreign_key_violations"]:
        print("FK violations  :", result["foreign_key_violations"][:5])

    if not args.apply:
        print(f"\nDry run. Inspect {out}, then re-run with --apply --backup.")
        return 0

    keep = args.db + ".corrupt"
    n = 1
    while os.path.exists(keep):
        n += 1
        keep = f"{args.db}.corrupt{n}"
    shutil.copy2(args.db, keep)
    print("Damaged file kept at:", keep)
    for suffix in ("-wal", "-shm"):
        if os.path.exists(args.db + suffix):
            os.remove(args.db + suffix)
    shutil.move(out, args.db)
    check = connect(args.db)
    print("Final integrity:", integrity(check)[:3])
    print("Final rows     :", {t: check.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
                               for t in ("raw_materials", "production_entries", "sales_challans")})
    check.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
