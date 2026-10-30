"""Undo for imports via pre-import safety copies.

Every import snapshots the DB first; the newest import can be rolled back
wholesale through its safety copy. Run:
python -m pytest backend/tests/test_import_undo.py -v
"""

import json
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _req():
    return SimpleNamespace(headers={}, client=None)


def _use_tmp_backups(db, tmp_path):
    db.add(models.ApplicationSetting(key="backup.folder", value=str(tmp_path)))
    db.commit()


def _import_audit(db, vals):
    db.add(models.AuditLog(
        module="import", action="create", record_type="import_suppliers",
        new_values=json.dumps(vals),
    ))
    db.commit()


def _backup_rec(db, folder, name, days_ago=0):
    (folder / name).write_bytes(b"x")
    rec = models.BackupRecord(
        filename=name, file_path=str(folder / name), file_size=1,
        backup_date=datetime(2026, 9, 1) + timedelta(days=days_ago),
        description="Pre-import safety copy", is_auto=False,
    )
    db.add(rec)
    db.commit()
    return rec


def test_no_import_nothing_to_undo(db):
    assert m.import_undo_status(db)["available"] is False


def test_legacy_import_without_safety_copy(db):
    _import_audit(db, {"filename": "old.xlsx", "imported": 2})
    assert m.import_undo_status(db)["available"] is False


def test_undo_available_with_backup_on_disk(db, tmp_path):
    _use_tmp_backups(db, tmp_path)
    rec = _backup_rec(db, tmp_path, "pre-import_20260101_120000.db")
    _import_audit(db, {"filename": "sup.xlsx", "imported": 3,
                       "pre_import_backup_id": rec.id,
                       "pre_import_backup": rec.filename})
    out = m.import_undo_status(db)
    assert out["available"] is True, out
    assert out["import_type"] == "suppliers", out
    assert out["imported_file"] == "sup.xlsx", out
    assert out["backup_id"] == rec.id, out


def test_missing_backup_file_not_undoable(db, tmp_path):
    _use_tmp_backups(db, tmp_path)
    rec = models.BackupRecord(
        filename="pre-import_20260101_120000.db", file_path="gone",
        file_size=1, backup_date=datetime(2026, 9, 1),
        description="Pre-import safety copy", is_auto=False,
    )
    db.add(rec)
    db.commit()
    _import_audit(db, {"filename": "sup.xlsx", "imported": 1,
                       "pre_import_backup": rec.filename})
    assert m.import_undo_status(db)["available"] is False


def test_undo_with_nothing_raises_404(db):
    with pytest.raises(HTTPException) as exc:
        m.undo_last_import(_req(), db)
    assert exc.value.status_code == 404


def test_import_takes_safety_copy_first(db, monkeypatch):
    order = []

    def fake_backup(_db, **kw):
        order.append("backup")
        assert kw["prefix"] == "pre-import_", kw
        assert "suppliers" in kw["description"] and "s.xlsx" in kw["description"]
        return SimpleNamespace(id=77, filename="pre-import_20260102_120000.db")

    def fake_run(_db, import_type, _content, _sheet=None):
        order.append("run")
        return {"import_type": import_type, "total_rows": 1, "imported": 1,
                "skipped": 0, "failed": 0, "success": True,
                "all_skipped": False, "warning": None,
                "errors": [], "skipped_rows": []}

    monkeypatch.setattr(m, "_do_backup", fake_backup)
    monkeypatch.setattr(m, "run_import", fake_run)

    async def fake_read():
        return b"fake"

    import asyncio
    out = asyncio.run(m.import_data(
        "suppliers", _req(),
        SimpleNamespace(filename="s.xlsx", read=fake_read), None, db))
    assert order == ["backup", "run"], order
    assert out["pre_import_backup"] == "pre-import_20260102_120000.db", out
    log = db.query(models.AuditLog).filter(
        models.AuditLog.module == "import").order_by(
        models.AuditLog.id.desc()).first()
    vals = json.loads(log.new_values)
    assert vals["pre_import_backup_id"] == 77, vals


def test_pre_import_backups_pruned_to_five(db, tmp_path):
    _use_tmp_backups(db, tmp_path)
    for i in range(7):
        _backup_rec(db, tmp_path, f"pre-import_2026010{i + 1}_120000.db", days_ago=i)
    m._prune_pre_import_backups(db)
    # days_ago=i counts up from Sept 1, so files 03-07 are the newest five.
    left = db.query(models.BackupRecord).order_by(models.BackupRecord.filename).all()
    assert [r.filename for r in left] == [
        f"pre-import_2026010{i}_120000.db" for i in (3, 4, 5, 6, 7)], left
    assert not (tmp_path / "pre-import_20260101_120000.db").exists()
    assert not (tmp_path / "pre-import_20260102_120000.db").exists()


def test_do_backup_uses_prefix(db, tmp_path, monkeypatch):
    live = tmp_path / "live.db"
    import sqlite3
    sqlite3.connect(str(live)).close()
    monkeypatch.setattr(m, "_live_db_path", lambda: live)
    _use_tmp_backups(db, tmp_path)
    rec = m._do_backup(db, is_auto=False, description="x", prefix="pre-import_")
    assert rec.filename.startswith("pre-import_"), rec.filename
    assert (tmp_path / rec.filename).exists()


def test_exact_import_routes_win_over_parameterized():
    # Regression: POST /import/undo was swallowed by /import/{import_type}
    # (as import_type="undo") and answered 422 "file: Field required",
    # so undo never executed although the confirm dialog appeared.
    from fastapi.routing import APIRoute

    def _collect(routes):
        """Recursively collect APIRoutes, descending into IncludedRouters."""
        out = []
        for r in routes:
            if isinstance(r, APIRoute):
                out.append(r)
            elif hasattr(r, "original_router"):
                out.extend(_collect(r.original_router.routes))
            elif hasattr(r, "routes"):
                out.extend(_collect(r.routes))
        return out

    order = []
    for r in _collect(m.app.routes):
        if r.path.startswith("/import/"):
            order.append((sorted(r.methods or []), r.path, r.endpoint.__name__))
    by_path = {(tuple(mm), p): e for mm, p, e in order}
    assert by_path.get((("POST",), "/import/undo")) == "undo_last_import", order
    idx_undo = next(i for i, (mm, p, e) in enumerate(order)
                    if p == "/import/undo" and "POST" in mm)
    idx_param = next(i for i, (mm, p, e) in enumerate(order)
                     if p == "/import/{import_type}" and "POST" in mm)
    assert idx_undo < idx_param, order
    idx_status = next(i for i, (mm, p, e) in enumerate(order)
                      if p == "/import/undo-status")
    assert idx_status < idx_param, order


def test_undo_freezes_button_until_restart():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "import.html"), encoding="utf-8") as fh:
        html = fh.read()
    i = html.find("window.undoLastImport")
    assert i >= 0
    body = html[i:i + 4000]
    assert 'getElementById("undo-btn").disabled = true' in body
    assert "until restart" in body
