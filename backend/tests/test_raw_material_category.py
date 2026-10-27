"""Raw-material categories show in the stock list and are settable from the form.

The category lived only in the DB/import path: the stock table had no
Category column, the form had no picker, and the API served no names.

Run: python -m pytest backend/tests/test_raw_material_category.py -v
"""

import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402


def _req():
    return SimpleNamespace(headers={}, client=None)


def test_create_and_list_categories(db):
    c = m.create_raw_material_category(
        m.RawMaterialCategoryCreate(name="Packaging"), _req(), db)
    assert c.name == "Packaging"
    rows = m.list_raw_material_categories(db)
    assert [r.name for r in rows] == ["Packaging"]


def test_duplicate_name_is_409(db):
    m.create_raw_material_category(
        m.RawMaterialCategoryCreate(name="Spices"), _req(), db)
    with pytest.raises(HTTPException) as exc:
        m.create_raw_material_category(
            m.RawMaterialCategoryCreate(name="spices"), _req(), db)
    assert exc.value.status_code == 409


def test_blank_name_is_400(db):
    with pytest.raises(HTTPException) as exc:
        m.create_raw_material_category(
            m.RawMaterialCategoryCreate(name="  "), _req(), db)
    assert exc.value.status_code == 400


def test_material_list_carries_category_name(db):
    cat = m.create_raw_material_category(
        m.RawMaterialCategoryCreate(name="Spices"), _req(), db)
    m.create_raw_material(
        m.RawMaterialCreate(material_id="RM-CAT1", name="Cat Haldi",
                            category_id=cat.id, unit="kg"),
        _req(), db,
    )
    rows = m.list_raw_materials(db)
    row = next(r for r in rows if r.material_id == "RM-CAT1")
    assert row.category_name == "Spices", row
    one = m.get_raw_material(row.id, db)
    assert one.category_name == "Spices", one


def test_stock_list_renders_category_column():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(
        os.path.join(root, "frontend", "pages", "raw-materials.html"), encoding="utf-8"
    ) as fh:
        html = fh.read()
    i = html.find("function renderRawTable")
    assert i >= 0
    body = html[i:i + 2500]
    assert "category_name" in body, "stock list still hides the category"
    assert 'id="raw-cat"' in html, "raw material form has no category picker"
