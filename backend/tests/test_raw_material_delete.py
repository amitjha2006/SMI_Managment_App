"""Raw material soft-delete (deactivate, never hard-delete).

A wrongly entered material is removed from pickers via is_active=False,
mirroring suppliers/customers/products. History on issued documents is
preserved; FK enforcement would refuse a hard delete anyway.

Run: python -m pytest backend/tests/test_raw_material_delete.py -v
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402
from backend.app.models.raw_material import RawMaterial  # noqa: E402


def _req():
    return SimpleNamespace(query_params={"reason": "test change note"}, headers={}, client=None)


def _material(db):
    mat = models.RawMaterial(material_id="RM-X1", name="Wrong Entry", unit="kg")
    db.add(mat)
    db.commit()
    return mat


def test_delete_deactivates_and_hides_from_list(db):
    mat = _material(db)
    out = m.delete_raw_material(mat.id, _req(), db)
    assert out["status"] == "deleted", out
    kept = db.query(RawMaterial).filter(RawMaterial.id == mat.id).first()
    assert kept is not None and kept.is_active is False, "history row must survive"
    assert all(r.id != mat.id for r in m.list_raw_materials(db))
    with pytest.raises(HTTPException) as e:
        m.get_raw_material(mat.id, db)
    assert e.value.status_code == 404


def test_delete_missing_product_404(db):
    with pytest.raises(HTTPException) as e:
        m.delete_raw_material(99999, _req(), db)
    assert e.value.status_code == 404


def test_delete_already_deleted(db):
    mat = _material(db)
    m.delete_raw_material(mat.id, _req(), db)
    out = m.delete_raw_material(mat.id, _req(), db)
    assert out["status"] == "already deleted", out


def test_edit_after_delete_404(db):
    from backend.app.schemas import RawMaterialUpdate

    mat = _material(db)
    m.delete_raw_material(mat.id, _req(), db)
    with pytest.raises(HTTPException) as e:
        m.update_raw_material(mat.id, RawMaterialUpdate(name="Fixed"), _req(), db)
    assert e.value.status_code == 404


def test_delete_ui_pins():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(
        os.path.join(root, "frontend", "pages", "raw-materials.html"), encoding="utf-8"
    ) as fh:
        inv = fh.read()
    for needle in ("deleteRaw", 'method: "DELETE"'):
        assert needle in inv, f"raw material remove action missing: {needle}"
