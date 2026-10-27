"""Audit-log retention: the trail is capped, not infinite.

Notifications already prune to the last 100 on write; audit_logs had no
cap at all and is the fastest-growing table. _audit keeps the newest
_MAX_AUDIT_KEPT rows and drops the rest, inside its own swallow so a
prune failure can never break the business operation being audited.

Run: python -m pytest backend/tests/test_audit_retention.py -v
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
import backend.app.core.shared as shared  # noqa: E402
from backend.app import models  # noqa: E402


def _req():
    return SimpleNamespace(headers={}, client=None)


def test_audit_log_keeps_only_the_newest_rows(db, monkeypatch):
    monkeypatch.setattr(shared, "_MAX_AUDIT_KEPT", 3)
    for i in range(5):
        m._audit(db, _req(), "test", "create", record_type="widget",
                 new_values={"n": i})
    db.commit()
    rows = (
        db.query(models.AuditLog)
        .order_by(models.AuditLog.id.desc())
        .all()
    )
    assert [r.new_values for r in rows] == [
        '{"n": 4}', '{"n": 3}', '{"n": 2}',
    ], [r.new_values for r in rows]
