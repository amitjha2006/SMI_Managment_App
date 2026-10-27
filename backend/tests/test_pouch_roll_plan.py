"""Every pack size ends up pointing at its own pouch roll.

The repair script plans three phases in one run: rename the hand-typed rolls
into one scheme, create a pouch raw material for a size that has none, then
map the size to it. Loose Powder shares one 1 kg pouch instead of getting a
printed roll per product, inactive products are left alone, and a printing
cylinder is never mistaken for a pouch.

Run: python -m pytest backend/tests/test_pouch_roll_plan.py -v
"""

import importlib.util
import os

import pytest

from backend.app import models

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCRIPT = os.path.join(_ROOT, "scripts", "seed_wrapper_mappings.py")


@pytest.fixture
def seeder():
    spec = importlib.util.spec_from_file_location("seed_wrapper_mappings", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _category(db, name):
    cat = models.RawMaterialCategory(name=name)
    db.add(cat)
    db.flush()
    return cat


def _product(db, code, name, sizes, active=True):
    product = models.Product(product_id=code, name=name, is_active=active)
    db.add(product)
    db.flush()
    for size_name in sizes:
        db.add(
            models.PackagingSize(
                product_id=product.id,
                size_name=size_name,
                weight_in_kg=0.05,
                packets_per_unit=1,
                wrapper_weight_kg=0.004,
                is_active=True,
            )
        )
    db.flush()
    return product


def _roll(db, code, name, category):
    material = models.RawMaterial(
        material_id=code, name=name, category_id=category.id, unit="KG", current_stock=0
    )
    db.add(material)
    db.flush()
    return material


def test_pouch_name_drops_powder_and_keeps_the_hindi(seeder):
    product = models.Product(product_id="P-X", name="Red Chili Powder (लाल मिर्च पाउडर)")
    assert seeder.pouch_name(product, "50g") == "Pouch Red Chili (लाल मिर्च) 50g"
    plain = models.Product(product_id="P-Y", name="Gram Flour (बेसन)")
    assert seeder.pouch_name(plain, "200g") == "Pouch Gram Flour (बेसन) 200g"


def test_a_size_without_a_roll_gets_one_and_loose_shares_theirs(db, seeder):
    _category(db, "Packaging")
    product = _product(
        db, "P-1", "Coriander Powder (धनिया पाउडर)", ["50g", "100g", "Loose Powder"]
    )
    plan = seeder.build_plan(db)

    assert [name for name, _sizes in plan["create"]] == [
        "Pouch Coriander (धनिया) 50g",
        "Pouch Coriander (धनिया) 100g",
        "Pouch Loose Powder 1kg",
    ]
    assert len(plan["map"]) == 3
    assert {name for _size, name in plan["map"] if name.startswith("Pouch Loose")} == {
        "Pouch Loose Powder 1kg"
    }
    sizes = {s.size_name: s for s in product.packaging_sizes}
    assert sizes["50g"].wrapper_material_id is None, "a dry plan must not write"

    seeder.apply_plan(db, plan)
    pouch = db.get(models.RawMaterial, sizes["50g"].wrapper_material_id)
    assert pouch.name == "Pouch Coriander (धनिया) 50g"
    assert pouch.category_id == seeder.category_id(db, "Packaging")
    assert pouch.hsn_code == seeder.POUCH_HSN

    again = seeder.build_plan(db)
    assert again["rename"] == [] and again["create"] == [] and again["map"] == []
    assert len(again["already"]) == 3


def test_loose_sizes_share_one_pouch_across_products(db, seeder):
    _category(db, "Packaging")
    _product(db, "P-1", "Cumin Powder (जीरा पाउडर)", ["Loose Powder"])
    _product(db, "P-2", "Turmeric Powder (हल्दी पाउडर)", ["Loose Powder"])
    plan = seeder.build_plan(db)
    assert [name for name, _sizes in plan["create"]] == ["Pouch Loose Powder 1kg"]
    assert len(plan["create"][0][1]) == 2, "both loose sizes hang off the shared pouch"


def test_inactive_products_are_skipped_except_cumin_seeds_loose(db, seeder):
    _category(db, "Packaging")
    _product(db, "P-1", "Chaat Masala (चाट मसाला)", ["250g"], active=False)
    _product(db, "P-2", "Cumin seeds (जीरा )", ["Loose Powder"], active=False)
    plan = seeder.build_plan(db)

    assert [name for name, _sizes in plan["create"]] == ["Pouch Loose Powder 1kg"]
    skipped = {f"{p.name} / {s.size_name}" for p, s, _r in plan["skip"]}
    assert skipped == {"Chaat Masala (चाट मसाला) / 250g"}


def test_a_printing_cylinder_is_never_a_pouch_roll(db, seeder):
    packaging = _category(db, "Packaging")
    cylinder = _category(db, "Cylinder")
    _roll(db, "RM-C1", "Gravure Prining Cylinder, Turmeric ,250g", cylinder)
    _product(db, "P-1", "Turmeric Powder (हल्दी पाउडर)", ["250g"])
    plan = seeder.build_plan(db)

    assert [name for name, _sizes in plan["create"]] == ["Pouch Turmeric (हल्दी) 250g"]
    rename = [new for _roll_obj, new in plan["rename"]]
    assert rename == ["Gravure Printing Cylinder, Turmeric ,250g"]

    seeder.apply_plan(db, plan)
    size = db.query(models.PackagingSize).one()
    mapped = db.get(models.RawMaterial, size.wrapper_material_id)
    assert mapped.category_id == packaging.id
    assert mapped.name == "Pouch Turmeric (हल्दी) 250g"
    drum = (
        db.query(models.RawMaterial)
        .filter(models.RawMaterial.name.like("Gravure%"))
        .one()
    )
    assert drum.category_id == cylinder.id
    assert drum.name == "Gravure Printing Cylinder, Turmeric ,250g"


def test_hand_typed_pouch_names_are_renamed_into_the_scheme(db, seeder):
    packaging = _category(db, "Packaging")
    _roll(db, "RM-1002", "Pauch Packet label red chili 50g", packaging)
    _roll(db, "RM-1005", "Pauch Packet label", packaging)
    _product(db, "P-1", "Red Chili Powder (लाल मिर्च पाउडर)", ["50g"])

    plan = seeder.build_plan(db)
    renames = {
        seeder.norm(roll.name): new for roll, new in plan["rename"]
    }
    assert renames == {
        "Pauch Packet label red chili 50g": "Pouch Red Chili (लाल मिर्च) 50g",
        "Pauch Packet label": "Pouch Loose Powder 1kg",
    }
    assert plan["create"] == [], "the roll already exists, so no duplicate is made"
