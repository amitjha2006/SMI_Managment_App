"""Regression: updating a product with a new category name must create the
category (get-or-create) instead of 400ing "Category 'X' not found".

The product editor sends `category: "<new name>"` when the user picks "+ New
category…" (frontend/pages/inventory.html:741-743), but update_product only
looked up existing categories and rejected anything new — contradicting the
schema comment that the server "resolves it to (or creates) a real
product_categories row" (backend/app/schemas/__init__.py:464-465).

Run: python -m pytest backend/tests/test_product_update_new_category.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from types import SimpleNamespace

from backend.app import models
from backend.app.routes.products import update_product
from backend.app.schemas import ProductUpdate


def _req():
    return SimpleNamespace(headers={}, client=None, query_params={"reason": "test change note"})


def _product(db, category_id=None):
    p = models.Product(
        product_id="P-U1",
        name="Test Masala",
        category_id=category_id,
        base_rate_per_kg=100,
        gst_rate=0,
        hsn_code=None,
    )
    db.add(p)
    db.commit()
    return p


def test_update_creates_new_category(db):
    # No categories exist yet; the new name must be created, not rejected.
    p = _product(db)
    before_ids = {c.id for c in db.query(models.ProductCategory).all()}
    res = update_product(p.id, ProductUpdate(category="New Spice Blend"), _req(), db)
    assert res.category_id is not None
    cat = db.query(models.ProductCategory).filter_by(name="New Spice Blend").one()
    assert res.category_id == cat.id
    assert {c.id for c in db.query(models.ProductCategory).all()} - before_ids == {cat.id}
    assert res.name == "Test Masala"


def test_update_uses_existing_category_without_duplicate(db):
    cat = models.ProductCategory(name="Dry")
    db.add(cat)
    db.commit()
    p = _product(db)
    res = update_product(p.id, ProductUpdate(category_id=cat.id), _req(), db)
    assert res.category_id == cat.id
    assert db.query(models.ProductCategory).count() == 1
