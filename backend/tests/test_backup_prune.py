"""Backup history self-heals: records without files disappear on listing.

Deleting backup files on disk (or a custom backup folder moving) leaves
BackupRecord rows pointing at nothing; downloading one then 404s. Listing
the history drops those orphan rows first, so the page always matches
what is actually restorable. A prune failure never breaks the listing.

Run: python -m pytest backend/tests/test_backup_prune.py -v
"""

import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402
from backend.app.routes import backup as backup_routes  # noqa: E402


def _record(db, filename):
    db.add(
        models.BackupRecord(
            filename=filename,
            file_path=filename,
            file_size=100,
            backup_date=datetime(2026, 9, 10, 8, 0, 0),
        )
    )
    db.commit()


def test_listing_drops_records_whose_files_are_gone(db, temp_dir, monkeypatch):
    live = Path(temp_dir) / "auto_20261005_120000.db"
    live.write_bytes(b"live-backup-bytes")
    monkeypatch.setattr(backup_routes, "_backup_dir", lambda _db=None: Path(temp_dir))
    _record(db, "auto_20261005_120000.db")
    _record(db, "auto_20250901_000000.db")
    _record(db, "backup_20250801_000000.db")

    rows = m.list_backup_records(db=db)

    assert [r.filename for r in rows] == ["auto_20261005_120000.db"], [
        r.filename for r in rows
    ]
    assert live.exists(), "the surviving backup file itself must stay"


def test_listing_survives_a_prune_failure(db, monkeypatch):
    _record(db, "auto_20250901_000000.db")

    def _boom(_db=None):
        raise OSError("disk gone")

    monkeypatch.setattr(backup_routes, "_backup_dir", _boom)
    rows = m.list_backup_records(db=db)
    assert isinstance(rows, list), "listing must never break on prune failure"
