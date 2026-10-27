"""Product formulation ratios (mix masala).

Each product can keep one mix ratio: raw materials with shares totalling
100%. The ratio is a mixing reference (and prefills production) that never
moves stock. Cost is indicative, at current purchase rates.

Run: python -m pytest backend/tests/test_formulation.py -v
"""

import os
import sys
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _req():
    return SimpleNamespace(query_params={"reason": "test change note"}, headers={}, client=None)


def _setup(db):
    mats = []
    for code, name, rate in (("RM-F1", "Coriander", "100"),
                            ("RM-F2", "Cumin", "200"),
                            ("RM-F3", "Pepper", "300")):
        mat = models.RawMaterial(
            material_id=code, name=name, unit="kg",
            current_stock=Decimal("1000"), purchase_rate=Decimal(rate),
        )
        db.add(mat)
        mats.append(mat)
    prod = models.Product(product_id="P-F1", name="Garam Masala")
    db.add(prod)
    db.commit()
    return {"mats": mats, "prod": prod}


def _put(db, prod_id, items):
    return m.set_product_formulation(
        prod_id, {"items": items}, _req(), db)


def test_empty_formulation_by_default(db):
    s = _setup(db)
    out = m.get_product_formulation(s["prod"].id, db)
    assert out["items"] == [], out
    assert Decimal(str(out["estimated_cost_per_kg"])) == Decimal("0"), out


def test_save_and_cost(db):
    s = _setup(db)
    ids = [x.id for x in s["mats"]]
    out = _put(db, s["prod"].id, [
        {"raw_material_id": ids[0], "percentage": 40},
        {"raw_material_id": ids[1], "percentage": 35},
        {"raw_material_id": ids[2], "percentage": 25},
    ])
    assert [i["material_name"] for i in out["items"]] == [
        "Coriander", "Cumin", "Pepper"], out
    # 0.40*100 + 0.35*200 + 0.25*300 = 185
    assert Decimal(str(out["estimated_cost_per_kg"])) == Decimal("185"), out
    again = m.get_product_formulation(s["prod"].id, db)
    assert len(again["items"]) == 3, again


def test_total_must_be_100(db):
    s = _setup(db)
    ids = [x.id for x in s["mats"]]
    with pytest.raises(HTTPException) as e:
        _put(db, s["prod"].id, [
            {"raw_material_id": ids[0], "percentage": 40},
            {"raw_material_id": ids[1], "percentage": 35},
        ])
    assert e.value.status_code == 400


def test_duplicate_material_refused(db):
    s = _setup(db)
    ids = [x.id for x in s["mats"]]
    with pytest.raises(HTTPException) as e:
        _put(db, s["prod"].id, [
            {"raw_material_id": ids[0], "percentage": 50},
            {"raw_material_id": ids[0], "percentage": 50},
        ])
    assert e.value.status_code == 400


def test_unknown_material_refused(db):
    s = _setup(db)
    with pytest.raises(HTTPException) as e:
        _put(db, s["prod"].id, [{"raw_material_id": 99999, "percentage": 100}])
    assert e.value.status_code == 400


def test_bad_percentages_refused(db):
    s = _setup(db)
    mid = s["mats"][0].id
    for pct in (0, -5, 101, "lots"):
        with pytest.raises(HTTPException) as e:
            _put(db, s["prod"].id, [{"raw_material_id": mid, "percentage": pct}])
        assert e.value.status_code == 400, pct


def test_empty_list_clears(db):
    s = _setup(db)
    ids = [x.id for x in s["mats"]]
    _put(db, s["prod"].id, [
        {"raw_material_id": ids[0], "percentage": 60},
        {"raw_material_id": ids[1], "percentage": 40},
    ])
    out = _put(db, s["prod"].id, [])
    assert out["items"] == [], out


def test_missing_product_404(db):
    with pytest.raises(HTTPException) as e:
        m.get_product_formulation(99999, db)
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        _put(db, 99999, [])
    assert e.value.status_code == 404


def test_editor_ui_pins():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "inventory.html"), encoding="utf-8") as fh:
        html = fh.read()
    for needle in ("se-formulation", "saveFormulation", "addFormulationRow",
                   "need 100%", "/formulation"):
        assert needle in html, f"formulation editor missing: {needle}"


def test_production_apply_pins():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "production.html"), encoding="utf-8") as fh:
        html = fh.read()
    assert "applyFormulation" in html, "no Apply formulation action on production"
    assert "pe-target" in html, "apply has no target qty to scale to"


def test_help_covers_formulation():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "help.html"), encoding="utf-8") as fh:
        html = fh.read()
    assert "Formulation" in html, "help never mentions mix ratios"
    assert "Apply formulation" in html, "help never mentions prefilling production"
    assert "processing steps" in html.lower(), "help never mentions formulation steps"
    assert "Help button" in html, "help never mentions the per-product help window"


def _put_steps(db, prod_id, steps):
    return m.set_product_formulation(
        prod_id, {"items": [], "steps": steps}, _req(), db)


def test_steps_saved_in_order(db):
    s = _setup(db)
    out = _put_steps(db, s["prod"].id, [
        {"stage": "roasting", "instruction": "Roast at 120C for 20 min"},
        {"stage": "", "instruction": "Cool fully before grinding"},
        {"stage": "grinding", "instruction": "Grind to 60 mesh"},
    ])
    got = [(x["stage"], x["instruction"]) for x in out["steps"]]
    assert got == [
        ("roasting", "Roast at 120C for 20 min"),
        (None, "Cool fully before grinding"),
        ("grinding", "Grind to 60 mesh"),
    ], got


def test_bad_steps_refused(db):
    s = _setup(db)
    with pytest.raises(HTTPException) as e:
        _put_steps(db, s["prod"].id, [{"stage": "freezing", "instruction": "Chill"}])
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        _put_steps(db, s["prod"].id, [{"stage": "mixing", "instruction": "   "}])
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        m.set_product_formulation(
            s["prod"].id, {"items": [], "steps": "mix well"}, _req(), db)
    assert e.value.status_code == 400


def test_steps_untouched_when_key_absent(db):
    s = _setup(db)
    _put_steps(db, s["prod"].id, [{"stage": "mixing", "instruction": "Mix 10 min"}])
    ids = [x.id for x in s["mats"]]
    m.set_product_formulation(s["prod"].id, {"items": [
        {"raw_material_id": ids[0], "percentage": 60},
        {"raw_material_id": ids[1], "percentage": 40},
    ]}, _req(), db)
    out = m.get_product_formulation(s["prod"].id, db)
    assert len(out["steps"]) == 1, out
    assert len(out["items"]) == 2, out


def test_steps_ui_pins():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "inventory.html"), encoding="utf-8") as fh:
        inv = fh.read()
    for needle in ("se-form-steps", "addFormulationStep", "renumberFormSteps"):
        assert needle in inv, f"steps editor missing: {needle}"
    with open(os.path.join(root, "frontend", "pages", "production.html"), encoding="utf-8") as fh:
        prod = fh.read()
    for needle in ("pe-formulation-guide", "refreshFormulationGuide"):
        assert needle in prod, f"production guidance missing: {needle}"


def _product(db):
    prod = models.Product(product_id="P-H1", name="Help Masala")
    db.add(prod)
    db.commit()
    return prod


def test_help_round_trip(db):
    p = _product(db)
    out = m.get_product_help(p.id, db)
    assert out["help_text"] is None, out
    saved = m.set_product_help(
        p.id, {"help_text": "Roast slow, grind fine."}, _req(), db)
    assert saved["help_text"] == "Roast slow, grind fine.", saved
    assert m.get_product_help(p.id, db)["help_text"] == "Roast slow, grind fine."


def test_help_blank_clears(db):
    p = _product(db)
    m.set_product_help(p.id, {"help_text": "  something  "}, _req(), db)
    out = m.set_product_help(p.id, {"help_text": "   "}, _req(), db)
    assert out["help_text"] is None, out


def test_help_missing_product_404(db):
    with pytest.raises(HTTPException) as e:
        m.get_product_help(99999, db)
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        m.set_product_help(99999, {"help_text": "x"}, _req(), db)
    assert e.value.status_code == 404


def test_help_rides_with_formulation_get(db):
    p = _product(db)
    m.set_product_help(p.id, {"help_text": "Keep it dry."}, _req(), db)
    out = m.get_product_formulation(p.id, db)
    assert out["help_text"] == "Keep it dry.", out


def test_formulation_put_leaves_help_alone(db):
    p = _product(db)
    m.set_product_help(p.id, {"help_text": "Keep it dry."}, _req(), db)
    out = m.get_product_formulation(p.id, db)
    assert out["help_text"] == "Keep it dry.", out


def test_help_migration_covers_new_column():
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "app" / "core" / "database.py").read_text(
        encoding="utf-8"
    )
    assert '("products", "help_text"' in src, (
        "help_text is missing from _ensure_columns: existing databases "
        "will crash with 'no such column' wherever products are read"
    )


def test_help_window_ui_pins():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "inventory.html"), encoding="utf-8") as fh:
        inv = fh.read()
    for needle in ("editProductHelp", "ph-text", "Product Help"):
        assert needle in inv, f"product help window missing: {needle}"
    with open(os.path.join(root, "frontend", "pages", "production.html"), encoding="utf-8") as fh:
        prod = fh.read()
    assert "help_text" in prod, "production guidance hides the product help"
