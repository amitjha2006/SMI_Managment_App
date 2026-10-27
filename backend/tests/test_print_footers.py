"""Tests for multiple print footers (terms & conditions).

The behaviour under test:

    The company profile holds one legacy footer. The print_footers master
    allows several; each challan freezes whichever text was picked (or the
    default, or the legacy text) into its own company_terms at creation, so
    editing footers later never rewrites an issued document.

Run: python -m pytest backend/tests/test_print_footers.py
"""

import pytest
from fastapi import HTTPException

from backend.app.models import ApplicationSetting, PrintFooter
from backend.app.core.shared import (
    _default_footer_id,
    _resolve_footer,
    _seed_default_footer,
)


def _setting(db, key, value):
    db.add(ApplicationSetting(key=key, value=value))
    db.commit()


def _footer(db, name, body="Terms text.", active=True):
    f = PrintFooter(name=name, body=body, is_active=active)
    db.add(f)
    db.commit()
    db.refresh(f)
    return f


def test_seed_creates_default_from_legacy_terms(db):
    _setting(db, "company.terms", "Goods once sold will not be taken back.")
    _seed_default_footer(db)
    rows = db.query(PrintFooter).all()
    assert len(rows) == 1
    assert rows[0].name == "Default"
    assert "will not be taken back" in rows[0].body


def test_seed_is_idempotent_and_needs_terms(db):
    _seed_default_footer(db)  # no company.terms -> nothing
    assert db.query(PrintFooter).count() == 0
    _setting(db, "company.terms", "Some terms.")
    _seed_default_footer(db)
    _seed_default_footer(db)
    assert db.query(PrintFooter).count() == 1


def test_explicit_active_footer_wins(db):
    a = _footer(db, "Standard", "Standard terms.")
    _footer(db, "Export", "Export terms.")
    fid, text = _resolve_footer(db, a.id)
    assert fid == a.id
    assert text == "Standard terms."


def test_unknown_or_inactive_footer_is_refused(db):
    _footer(db, "Standard", "Standard terms.", active=False)
    with pytest.raises(HTTPException):
        _resolve_footer(db, 999999)
    inactive = db.query(PrintFooter).filter(PrintFooter.name == "Standard").first()
    with pytest.raises(HTTPException):
        _resolve_footer(db, inactive.id)


def test_default_setting_is_used_when_nothing_picked(db):
    f = _footer(db, "Wholesale", "Wholesale terms.")
    _setting(db, "sales.footer_id", str(f.id))
    fid, text = _resolve_footer(db, None)
    assert fid == f.id
    assert text == "Wholesale terms."


def test_garbage_default_falls_back_to_legacy(db):
    _setting(db, "sales.footer_id", "not-a-number")
    assert _resolve_footer(db, None) == (None, "")
    assert _default_footer_id(db) is None


def test_no_footers_no_default_means_legacy(db):
    assert _resolve_footer(db, None) == (None, "")
