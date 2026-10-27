"""Default pouch weight on pack-size creation.

A new pack size should arrive with its empty-pouch weight already filled
(7g->2g, 50g->4g, 100g->6g, 175g->6g, 200g->8g, 250g->8g, 400g->10g,
500g->10g, 1kg->17g, Loose Powder->14g), still editable: an explicit value
in the payload wins, an unlisted weight stays 0 (wrapper accounting off), and
re-activating a removed size keeps its stored pouch weight instead of
resetting it. Excel import is out of scope by decision - it builds sizes
directly.

Run: python -m pytest backend/tests/test_pouch_weight_default.py -v
"""

import os
import sys
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _req():
    return SimpleNamespace(query_params={"reason": "test change note"}, headers={}, client=None)


def _product(db, name, sizes):
    return m.create_product({"name": name, "packaging_sizes": sizes}, _req(), db)


def _wrapper(db, product_id, size_name):
    ps = (
        db.query(models.PackagingSize)
        .filter(
            models.PackagingSize.product_id == product_id,
            models.PackagingSize.size_name == size_name,
        )
        .one()
    )
    return Decimal(str(ps.wrapper_weight_kg or 0))


def test_listed_weights_arrive_with_default_pouch(db):
    out = _product(db, "Pouch Default Masala", [
        {"size_name": "7g", "weight_in_kg": 0.007, "rate_per_kg": 100},
        {"size_name": "50g", "weight_in_kg": 0.05, "rate_per_kg": 100},
        {"size_name": "100g", "weight_in_kg": 0.1, "rate_per_kg": 100},
        {"size_name": "175g", "weight_in_kg": 0.175, "rate_per_kg": 100},
        {"size_name": "200g", "weight_in_kg": 0.2, "rate_per_kg": 100},
        {"size_name": "250g", "weight_in_kg": 0.25, "rate_per_kg": 100},
        {"size_name": "400g", "weight_in_kg": 0.4, "rate_per_kg": 100},
        {"size_name": "500g", "weight_in_kg": 0.5, "rate_per_kg": 100},
        {"size_name": "1kg", "weight_in_kg": 1.0, "rate_per_kg": 100},
    ])
    assert _wrapper(db, out.id, "7g") == Decimal("0.002")
    assert _wrapper(db, out.id, "50g") == Decimal("0.004")
    assert _wrapper(db, out.id, "100g") == Decimal("0.006")
    assert _wrapper(db, out.id, "175g") == Decimal("0.006")
    assert _wrapper(db, out.id, "200g") == Decimal("0.008")
    assert _wrapper(db, out.id, "250g") == Decimal("0.008")
    assert _wrapper(db, out.id, "400g") == Decimal("0.010")
    assert _wrapper(db, out.id, "500g") == Decimal("0.010")
    assert _wrapper(db, out.id, "1kg") == Decimal("0.017")


def test_loose_powder_gets_its_own_bag_not_the_kilo_default(db):
    """Loose powder is stored at 1 kg like a real 1kg pack, so the weight
    alone cannot tell them apart - the name decides."""
    out = _product(db, "Pouch Loose Masala", [
        {"size_name": "Loose Powder", "weight_in_kg": 1.0, "rate_per_kg": 100},
    ])
    assert _wrapper(db, out.id, "Loose Powder") == Decimal("0.014")


def test_explicit_pouch_weight_wins_over_default(db):
    out = _product(db, "Pouch Override Masala", [
        {"size_name": "250g", "weight_in_kg": 0.25, "rate_per_kg": 100,
         "wrapper_weight_kg": 0.02},
    ])
    assert _wrapper(db, out.id, "250g") == Decimal("0.02")


def test_unlisted_weight_keeps_wrapper_off(db):
    out = _product(db, "Pouch Odd Masala", [
        {"size_name": "750g", "weight_in_kg": 0.75, "rate_per_kg": 100},
    ])
    assert _wrapper(db, out.id, "750g") == Decimal("0")


def test_reactivated_size_keeps_stored_pouch_weight(db):
    out = _product(db, "Pouch Revive Masala", [
        {"size_name": "250g", "weight_in_kg": 0.25, "rate_per_kg": 100,
         "wrapper_weight_kg": 0.009},
        {"size_name": "500g", "weight_in_kg": 0.5, "rate_per_kg": 100},
    ])
    size = (
        db.query(models.PackagingSize)
        .filter(
            models.PackagingSize.product_id == out.id,
            models.PackagingSize.size_name == "250g",
        )
        .one()
    )
    m.delete_packaging_size(size.id, _req(), db)
    _product(db, "Pouch Revive Masala", [
        {"size_name": "250g", "weight_in_kg": 0.25, "rate_per_kg": 100},
    ])
    assert _wrapper(db, out.id, "250g") == Decimal("0.009")


def test_repair_script_table_matches_the_backend():
    """scripts/fix_pouch_weights.py keeps its own copy of the table, because
    importing backend.main would run init_db() against the repo database.
    The copies must not drift."""
    import importlib.util

    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "scripts",
        "fix_pouch_weights.py",
    )
    spec = importlib.util.spec_from_file_location("fix_pouch_weights", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    backend_map = {
        int(k * 1000): int(v * 1000) for k, v in m._DEFAULT_POUCH_KG.items()
    }
    assert backend_map == mod.POUCH_G_BY_SIZE
    assert Decimal(str(mod.LOOSE_POUCH_G)) == m._LOOSE_POUCH_KG * 1000
    assert Decimal(str(mod.LOOSE_POUCH_G)) == Decimal("14")
