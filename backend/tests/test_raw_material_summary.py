"""Raw-material inventory summary (screenshot table) + alphabetical dropdowns.

The factory tracks raw-material inventory as one row per material:
Stock in Kg (opening + confirmed purchases in range), Production in Kg
(consumed by production in range), Stock Balance, Wastage in Kg and %.
Wastage per material is derived from the batches' stage entries,
allocated pro-rata over what each batch consumed, because the
production_wastage table is never written by the application.

Run: python -m pytest backend/tests/test_raw_material_summary.py -v
"""

import os
import sys
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _req():
    return SimpleNamespace(headers={}, client=None)


def _mat(db, code, name, opening=0):
    mat = models.RawMaterial(
        material_id=code, name=name, unit="kg",
        opening_stock=Decimal(str(opening)), current_stock=Decimal(str(opening)),
    )
    db.add(mat)
    db.commit()
    return mat


def _supplier(db, code="S-RM", name="RM Supplier"):
    s = models.Supplier(supplier_id=code, name=name, contact_number="9000000001")
    db.add(s)
    db.commit()
    return s


_inv_seq = [0]


def _invoice(db, sup, mat, day, qty, confirmed=True, archived=False):
    inv_date = datetime(2026, 9, day, 10, 0, 0) if isinstance(day, int) else day
    _inv_seq[0] += 1
    inv = models.PurchaseInvoice(
        invoice_number=f"PUR-{mat.material_id}-{inv_date.day}-{qty}-{_inv_seq[0]}",
        invoice_date=inv_date,
        supplier_id=sup.id, total_amount=Decimal("100"),
        paid_amount=Decimal("0"), outstanding_amount=Decimal("100"),
        is_confirmed=confirmed, is_archived=archived,
    )
    db.add(inv)
    db.commit()
    db.add(
        models.PurchaseInvoiceItem(
            purchase_invoice_id=inv.id, raw_material_id=mat.id,
            quantity=Decimal(str(qty)), unit="kg", rate=Decimal("50"),
            amount=Decimal("100"),
        )
    )
    db.commit()
    # Mirror the app: a confirmed purchase raises on-hand stock.
    if confirmed and not archived:
        mat.current_stock = Decimal(str(mat.current_stock or 0)) + Decimal(str(qty))
        db.commit()
    return inv


def _product(db, code="P-RM", name="RM Masala"):
    prod = models.Product(product_id=code, name=name)
    db.add(prod)
    db.flush()
    size = models.PackagingSize(
        product_id=prod.id, size_name="250g", weight_in_kg=Decimal("0.25"),
        rate_per_kg=Decimal("200"), packets_per_unit=1, is_active=True,
    )
    db.add(size)
    db.commit()
    return prod, size


def _produce(db, prod, size, consumed, stages, day=15):
    mat = models.RawMaterial(
        material_id="RM-ZZ", name="ZZ Filler", unit="kg",
        current_stock=Decimal("100000"),
    )
    db.add(mat)
    db.commit()
    payload = {
        "product_id": prod.id,
        "production_date": f"2026-09-{day:02d}T10:00:00",
        "packaging_size_id": size.id,
        "actual_output_packets": 400,
        "target_quantity": 100,
        "consumed_materials": [
            {"raw_material_id": mid, "quantity": qty} for mid, qty in consumed
        ],
        "stages": stages,
    }
    return m.create_production_entry(payload, _req(), db)


def _row(out, name):
    return next(r for r in out["rows"] if r["name"] == name)


def _adjust(db, mat, qty, reason="recount", day=12):
    out = m.create_stock_adjustment(
        {"kind": "raw_material", "raw_material_id": mat.id,
         "quantity": qty, "reason": reason},
        _req(), db,
    )
    assert out["status"] == "recorded", out
    # created_at defaults to now; pin the movement inside the test window.
    db.query(models.RawMaterialStockMovement).filter(
        models.RawMaterialStockMovement.raw_material_id == mat.id,
        models.RawMaterialStockMovement.movement_type == "adjustment",
    ).update({"created_at": datetime(2026, 9, day, 10, 0, 0)})
    db.commit()
    return out


def test_adjustment_in_range_raises_summary_stock(db):
    mat = _mat(db, "RM-ADJ", "Adjusted Seed", opening=100)
    _adjust(db, mat, 25)

    out = m.raw_material_summary(db, from_date="2026-09-01", to_date="2026-09-30")
    r = _row(out, "Adjusted Seed")
    assert r["stock_kg"] == 125.0, r
    assert r["balance_kg"] == 125.0, r


def test_negative_adjustment_lowers_summary_stock(db):
    mat = _mat(db, "RM-ADN", "Shrunk Seed", opening=100)
    _adjust(db, mat, -30, reason="damage")

    out = m.raw_material_summary(db, from_date="2026-09-01", to_date="2026-09-30")
    r = _row(out, "Shrunk Seed")
    assert r["stock_kg"] == 70.0, r
    assert r["balance_kg"] == 70.0, r


def test_out_of_range_adjustment_is_excluded(db):
    mat = _mat(db, "RM-ADO", "Old Adjust Seed", opening=100)
    _adjust(db, mat, 25)
    db.query(models.RawMaterialStockMovement).filter(
        models.RawMaterialStockMovement.raw_material_id == mat.id,
    ).update({"created_at": datetime(2026, 8, 5, 10, 0, 0)})
    db.commit()

    out = m.raw_material_summary(db, from_date="2026-09-01", to_date="2026-09-30")
    r = _row(out, "Old Adjust Seed")
    assert r["stock_kg"] == 100.0, r
    assert r["balance_kg"] == 100.0, r


def test_summary_math_matches_screenshot_semantics(db):
    sup = _supplier(db)
    turmeric = _mat(db, "RM-TUR", "Turmeric", opening=100)
    _invoice(db, sup, turmeric, 5, 50)
    prod, size = _product(db)
    _produce(
        db, prod, size, [(turmeric.id, 90)],
        [{"stage": "roasting", "input_quantity": 90, "output_quantity": 85}],
    )
    out = m.raw_material_summary(db, from_date="2026-09-01", to_date="2026-09-30")

    r = _row(out, "Turmeric")
    assert r["stock_kg"] == 150.0, r
    assert r["production_kg"] == 90.0, r
    assert r["balance_kg"] == 60.0, r
    assert r["wastage_kg"] == 5.0, r
    assert r["wastage_pct"] == 5.56, r  # 5 / 90 * 100, like the screenshot


def test_out_of_range_rows_are_excluded(db):
    sup = _supplier(db)
    mat = _mat(db, "RM-COR", "Coriander", opening=10)
    _invoice(db, sup, mat, datetime(2026, 8, 5, 10, 0, 0), 40)
    prod, size = _product(db)
    out1 = m.create_production_entry(
        {
            "product_id": prod.id, "production_date": "2026-08-10T10:00:00",
            "packaging_size_id": size.id, "actual_output_packets": 400,
            "target_quantity": 100,
            "consumed_materials": [{"raw_material_id": mat.id, "quantity": 20}],
            "stages": [{"stage": "roasting", "input_quantity": 20, "output_quantity": 19}],
        },
        _req(), db,
    )
    assert out1["id"]

    out = m.raw_material_summary(db, from_date="2026-09-01", to_date="2026-09-30")
    r = _row(out, "Coriander")
    assert r["stock_kg"] == 10.0, r
    assert r["production_kg"] == 0.0, r
    assert r["wastage_kg"] == 0.0, r


def test_unconfirmed_and_archived_purchases_are_excluded(db):
    sup = _supplier(db)
    mat = _mat(db, "RM-CHI", "Chili", opening=0)
    _invoice(db, sup, mat, 5, 40, confirmed=False)
    _invoice(db, sup, mat, 6, 60, archived=True)

    out = m.raw_material_summary(db, from_date="2026-09-01", to_date="2026-09-30")
    assert _row(out, "Chili")["stock_kg"] == 0.0, out["rows"]


def test_archived_production_is_excluded(db):
    sup = _supplier(db)
    mat = _mat(db, "RM-CUM", "Cumin", opening=0)
    _invoice(db, sup, mat, 5, 100)
    prod, size = _product(db)
    entry = _produce(
        db, prod, size, [(mat.id, 60)],
        [{"stage": "roasting", "input_quantity": 60, "output_quantity": 55}],
    )
    db.query(models.ProductionEntry).filter(
        models.ProductionEntry.id == entry["id"]
    ).update({"is_archived": True})
    db.commit()

    out = m.raw_material_summary(db, from_date="2026-09-01", to_date="2026-09-30")
    r = _row(out, "Cumin")
    assert r["production_kg"] == 0.0, r
    assert r["wastage_kg"] == 0.0, r
    assert r["balance_kg"] == 100.0, r


def test_batch_wastage_splits_pro_rata_over_consumed_materials(db):
    sup = _supplier(db)
    a = _mat(db, "RM-AA", "Alpha", opening=0)
    b = _mat(db, "RM-BB", "Beta", opening=0)
    _invoice(db, sup, a, 5, 60)
    _invoice(db, sup, b, 5, 40)
    prod, size = _product(db)
    _produce(
        db, prod, size, [(a.id, 60), (b.id, 40)],
        [{"stage": "roasting", "input_quantity": 100, "output_quantity": 90}],
    )

    out = m.raw_material_summary(db, from_date="2026-09-01", to_date="2026-09-30")
    assert _row(out, "Alpha")["wastage_kg"] == 6.0, out["rows"]
    assert _row(out, "Beta")["wastage_kg"] == 4.0, out["rows"]
    assert out["totals"]["wastage_kg"] == 10.0, out["totals"]


def test_unused_active_material_still_gets_a_zero_row(db):
    _mat(db, "RM-IDLE", "Idle Seed", opening=0)

    out = m.raw_material_summary(db, from_date="2026-09-01", to_date="2026-09-30")
    r = _row(out, "Idle Seed")
    assert (r["stock_kg"], r["production_kg"], r["balance_kg"]) == (0.0, 0.0, 0.0), r
    assert (r["wastage_kg"], r["wastage_pct"]) == (0.0, 0.0), r


def test_master_lists_come_back_alphabetical(db):
    for code, name in [("RM-Z", "Zebra"), ("RM-M", "Mango"), ("RM-A", "Apple")]:
        _mat(db, code, name)
    for code, name in [("P-Z", "Zulu"), ("P-A", "Alpha")]:
        db.add(models.Product(product_id=code, name=name))
    for code, name in [("C-Z", "Zed"), ("C-A", "Asha")]:
        db.add(models.Customer(customer_id=code, name=name,
                               contact_number="9000000003"))
    for code, name in [("S-Z", "Zed"), ("S-A", "Asha")]:
        db.add(models.Supplier(supplier_id=code, name=name,
                               contact_number="9000000002"))
    db.commit()

    assert [x.name for x in m.list_raw_materials(db)] == ["Apple", "Mango", "Zebra"]
    assert [x.name for x in m.list_products(db)] == ["Alpha", "Zulu"]
    assert [x.name for x in m.list_customers(db)] == ["Asha", "Zed"]
    assert [x.name for x in m.list_suppliers(db)] == ["Asha", "Zed"]


def test_production_history_detail_carries_materials_sizes_and_loss(db):
    sup = _supplier(db)
    mat = _mat(db, "RM-HD", "History Dhania", opening=0)
    _invoice(db, sup, mat, 5, 100)
    prod, size = _product(db, name="History Masala")
    entry = _produce(
        db, prod, size, [(mat.id, 100)],
        [{"stage": "roasting", "input_quantity": 100, "output_quantity": 94}],
    )

    d = m._production_to_dict(
        db.query(models.ProductionEntry).filter(
            models.ProductionEntry.id == entry["id"]).first()
    )
    assert d["outputs"], d
    assert d["outputs"][0]["packaging_size_name"] == "250g", d["outputs"]
    assert d["outputs"][0]["quantity_kg"] == 100.0, d["outputs"]
    assert d["stage_entries"][0].wastage_quantity == Decimal("6"), d["stage_entries"]

    mats = m.get_production_materials(entry["id"], db)
    assert len(mats) == 1, mats
    assert mats[0]["material_name"] == "History Dhania", mats
    assert mats[0]["quantity"] == 100.0, mats


def test_summary_page_calls_the_endpoint():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(
        os.path.join(root, "frontend", "pages", "raw-material-summary.html"), encoding="utf-8"
    ) as fh:
        html = fh.read()
    assert "/reports/raw-material-summary" in html, "summary page never calls the endpoint"


def test_dropdown_options_render_alphabetically():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "js", "components.js"), encoding="utf-8") as fh:
        js = fh.read()
    assert "sortByName" in js, "no shared alphabetical sorter for dropdowns"
    with open(os.path.join(root, "frontend", "pages", "production.html"), encoding="utf-8") as fh:
        prod_html = fh.read()
    assert "sortByName" in prod_html, "production material dropdown not sorted"
