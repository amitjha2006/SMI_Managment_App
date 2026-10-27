"""A product can hold each pack size only once.

Two `/products` requests 19 ms apart (a double-clicked "Add pack size") both
read the size list before either committed, so both passed the "already has
100g" check and the second one stored a duplicate Cumin 100g row. The rule now
lives in the database as a unique index on (product_id, size_name), and the
size insert runs as INSERT ... ON CONFLICT DO NOTHING so the losing request
reports the ordinary "already has 100g - not added again" instead of a 500 or
a second row.

Run: python -m pytest backend/tests/test_pack_size_unique.py -v
"""

import os
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _product(db, name="Cumin Powder"):
    product = models.Product(product_id="P-CUMIN", name=name, is_active=True)
    db.add(product)
    db.flush()
    return product


def _sizes(db, product, size_name="100g"):
    return (
        db.query(models.PackagingSize)
        .filter(
            models.PackagingSize.product_id == product.id,
            models.PackagingSize.size_name == size_name,
        )
        .all()
    )


def test_adding_the_same_size_twice_keeps_one_row(db):
    product = _product(db)

    added, skipped = m._attach_sizes_to_product(
        db, product, [{"size_name": "100g", "weight_in_kg": 0.1}]
    )
    assert (added, skipped) == (["100g"], [])

    added, skipped = m._attach_sizes_to_product(
        db, product, [{"size_name": "100g", "weight_in_kg": 0.1}]
    )
    assert (added, skipped) == ([], ["100g"]), "the second request must not add a row"
    db.commit()
    assert len(_sizes(db, product)) == 1


def test_the_database_refuses_the_losing_insert_of_a_race(db):
    """The handler's own check can be stale; the index has to hold anyway.

    The size collection is loaded empty first, then a row is inserted behind
    the ORM's back - which is what the other request committing first looks
    like. Without the index the handler would happily insert a duplicate.
    """
    product = _product(db)
    assert product.packaging_sizes == []  # cached empty, like the losing request
    db.execute(
        text(
            "insert into packaging_sizes (product_id, size_name, weight_in_kg, is_active) "
            "values (:pid, '100g', 0.1, 1)"
        ),
        {"pid": product.id},
    )

    added, skipped = m._attach_sizes_to_product(
        db, product, [{"size_name": "100g", "weight_in_kg": 0.1}]
    )
    assert (added, skipped) == ([], ["100g"]), "caught as a duplicate, not a 500"
    db.commit()  # a poisoned session would fail here
    assert len(_sizes(db, product)) == 1


def test_a_multi_size_payload_survives_a_duplicate_in_the_middle(db):
    """The winning sizes must still land; only the duplicate is skipped."""
    product = _product(db)
    m._attach_sizes_to_product(db, product, [{"size_name": "50g", "weight_in_kg": 0.05}])
    db.commit()
    _ = product.packaging_sizes  # cached before the race's stale read

    added, skipped = m._attach_sizes_to_product(
        db,
        product,
        [
            {"size_name": "250g", "weight_in_kg": 0.25},
            {"size_name": "50g", "weight_in_kg": 0.05},
            {"size_name": "500g", "weight_in_kg": 0.5},
        ],
    )
    assert added == ["250g", "500g"]
    assert skipped == ["50g"]
    db.commit()
    assert {s.size_name for s in db.query(models.PackagingSize).all()} == {
        "250g",
        "50g",
        "500g",
    }


def test_the_app_creates_the_index_on_startup():
    """Pinned as source: existing databases get the index from init_db."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "app",
        "core",
        "database.py",
    )
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    assert "create unique index uq_pack_size_per_product " in source
