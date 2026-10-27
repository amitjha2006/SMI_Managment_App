"""Two-step 'pack later' production flow.

A batch is saved unpacked (bulk kg only), then packed later: sizes, packets,
wrapper rolls and the pack date are recorded at packing time, which posts the
wrapper consumption and the finished stock.

Run: python -m pytest backend/tests/test_production_pack_later.py -v
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

import backend.main as m
from backend.app import models


def _req():
    return SimpleNamespace(headers={}, client=None)


@pytest.fixture
def setup(db):
    mat = models.RawMaterial(
        material_id="RM-PL1", name="Pack Later Raw", unit="kg",
        current_stock=Decimal("10000"), purchase_rate=Decimal("100"),
    )
    db.add(mat)
    prod = models.Product(product_id="P-PL1", name="Pack Later Masala")
    db.add(prod)
    db.flush()
    size = models.PackagingSize(
        product_id=prod.id, size_name="250g", weight_in_kg=Decimal("0.25"),
        rate_per_kg=Decimal("200"), packets_per_unit=1, wrapper_weight_kg=Decimal("0.01"),
        wrapper_material_id=None, is_active=True,
    )
    db.add(size)
    db.commit()
    return {"mat": mat, "prod": prod, "size": size}


def _save_unpacked(db, setup, stages=None, output_kg=50, target=50):
    return m.create_production_entry({
        "product_id": setup["prod"].id,
        "production_date": "2026-10-20T10:00:00",
        "target_quantity": target,
        "outputs": [],
        "consumed_materials": [{"raw_material_id": setup["mat"].id, "quantity": output_kg}],
        "stages": stages or [
            {"stage": "grinding", "input_quantity": output_kg, "output_quantity": output_kg},
        ],
        "actual_output": output_kg,
    }, _req(), db)


def test_unpacked_entry_has_no_size_and_is_in_progress(db, setup):
    out = _save_unpacked(db, setup)
    assert out["packaging_size_id"] is None
    assert out["packed_at"] is None
    assert out["status"] == "in_progress"
    assert out["actual_output"] == Decimal("50")


def test_unpacked_entry_posts_consumption_with_movement_date(db, setup):
    out = _save_unpacked(db, setup)
    mat_rows = m.get_production_materials(out["id"], db)
    assert len(mat_rows) == 1
    mv = (
        db.query(models.RawMaterialStockMovement)
        .filter_by(reference_id=out["id"])
        .first()
    )
    assert mv.movement_date is not None


def test_pack_requires_outputs(db, setup):
    out = _save_unpacked(db, setup)
    try:
        m.pack_production_entry(
            out["id"], {"packed_date": "2026-10-21T09:00:00", "outputs": []},
            _req(), db,
        )
        assert False, "should have raised"
    except m.HTTPException as e:
        assert e.status_code == 400


def test_pack_posts_wrapper_and_finished_stock(db, setup):
    out = _save_unpacked(db, setup)
    # Map the roll first so wrapper deduction works
    size = setup["size"]
    size.wrapper_material_id = setup["mat"].id
    db.commit()

    result = m.pack_production_entry(
        out["id"],
        {
            "packed_date": "2026-10-21T09:00:00",
            "outputs": [{"packaging_size_id": size.id, "packets": 200}],
            "wrapper_mappings": [],
        },
        _req(), db,
    )
    assert result["packed_at"] is not None
    assert result["status"] == "completed"
    assert result["packaging_size_id"] == size.id

    # Finished stock posted: 200 packets x 0.25 kg = 50 kg
    fin = (
        db.query(models.FinishedProductStockMovement)
        .filter_by(reference_id=out["id"])
        .all()
    )
    assert len(fin) == 1
    assert fin[0].quantity == Decimal("50")
    assert fin[0].movement_date is not None

    # Wrapper consumed: 200 packets x 0.01 kg = 2 kg from the roll
    mat = db.query(models.RawMaterial).filter_by(id=setup["mat"].id).first()
    # 50 kg consumed at save + 2 kg wrapper at pack = 52 kg total deduction
    assert mat.current_stock == Decimal("9948")

    raw_rows = (
        db.query(models.RawMaterialStockMovement)
        .filter_by(reference_id=out["id"])
        .all()
    )
    # One at save (50 kg), one at pack (2 kg wrapper)
    assert len(raw_rows) == 2


def test_pack_sets_packaging_stage_row(db, setup):
    out = _save_unpacked(db, setup)
    size = setup["size"]
    size.wrapper_material_id = setup["mat"].id
    db.commit()

    m.pack_production_entry(
        out["id"],
        {
            "packed_date": "2026-10-21T09:00:00",
            "outputs": [{"packaging_size_id": size.id, "packets": 200}],
            "wrapper_mappings": [],
        },
        _req(), db,
    )
    stage = (
        db.query(models.ProductionStageEntry)
        .filter_by(production_entry_id=out["id"], stage=models.ProductionStage.PACKAGING)
        .first()
    )
    assert stage is not None
    assert stage.input_quantity == Decimal("50")
    assert stage.output_quantity == Decimal("52")  # net + wrapper
    assert stage.packing_gain_kg == Decimal("2")


def test_pack_claims_atomically(db, setup):
    out = _save_unpacked(db, setup)
    size = setup["size"]
    size.wrapper_material_id = setup["mat"].id
    db.commit()

    m.pack_production_entry(
        out["id"],
        {
            "packed_date": "2026-10-21T09:00:00",
            "outputs": [{"packaging_size_id": size.id, "packets": 200}],
            "wrapper_mappings": [],
        },
        _req(), db,
    )
    # Second pack attempt should fail
    try:
        m.pack_production_entry(
            out["id"],
            {
                "packed_date": "2026-10-21T10:00:00",
                "outputs": [{"packaging_size_id": size.id, "packets": 100}],
                "wrapper_mappings": [],
            },
            _req(), db,
        )
        assert False, "should have raised on double pack"
    except m.HTTPException as e:
        assert e.status_code == 400


def test_pack_dialog_uses_loaded_products_not_missing_route():
    """GET /products/{id} does not exist (that path is PUT/DELETE only), so
    fetching it returns 405 'Method Not Allowed' the moment the dialog opens.
    The dialog must read pack sizes from the products list already on the page.
    """
    from pathlib import Path

    html = (
        Path(m.__file__).resolve().parents[1] / "frontend" / "pages" / "production.html"
    ).read_text(encoding="utf-8")
    start = html.index("window.openPackDialog")
    pack = html[start:html.index("window.closePack", start)]
    assert '"/products/" +' not in pack
    assert "activeSizesFor(" in pack


def test_status_guard_blocks_completed_while_unpacked(db, setup):
    out = _save_unpacked(db, setup)
    try:
        m.set_production_status(
            out["id"], {"status": "completed"}, db,
        )
        assert False, "should have raised"
    except m.HTTPException as e:
        assert e.status_code == 400
        assert "not packed yet" in e.detail
