"""Perf quick wins (P1-P3) + dark theme toggle.

P1: unbounded tables paginate (followup, both adjustment histories).
P2: the dashboard auto-refresh tick reloads stats only; the full fan-out
    stays manual.
P3: /stock-adjustments resolves names with bulk maps, not per-row queries;
    output (including the #id fallbacks) is unchanged.
Theme: header toggle + data-theme dark overrides, persisted per user with
    "light" as the default for old rows.

Run: python -m pytest backend/tests/test_perf_theming.py -v
"""

import os
import sys
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402
from backend.app.models.finished_product import FinishedProductMovementType  # noqa: E402


def _read(relpath):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, *relpath.split("/")), encoding="utf-8") as fh:
        return fh.read()


def test_unbounded_tables_paginate():
    for page, table in [
        ("frontend/pages/followup.html", "followup-table"),
        ("frontend/pages/inventory.html", "adj-table"),
        ("frontend/pages/raw-materials.html", "adj-table"),
    ]:
        html = _read(page)
        anchor = html.find(f'getElementById("{table}")')
        assert anchor >= 0, f"{table} render gone from {page}"
        assert "pageSize" in html[anchor:anchor + 1500], f"{table} renders every row"


def test_dashboard_tick_is_stats_only():
    dash = _read("frontend/pages/dashboard.html")
    assert "refreshDashboardLight" in dash, "no light refresh path"
    assert "AppPageRefreshers" in dash, "tick still runs the full fan-out"
    app_js = _read("frontend/js/app.js")
    assert "AppPageRefreshers" in app_js, "tick ignores the light path"


def _seed_adjustments(db):
    mat = models.RawMaterial(
        material_id="RM-P3", name="P3 Spice", unit="kg",
        opening_stock=Decimal("10"), current_stock=Decimal("10"),
    )
    db.add(mat)
    db.flush()
    db.add(models.RawMaterialStockMovement(
        raw_material_id=mat.id, movement_type="adjustment",
        quantity=Decimal("2"), unit="kg", notes="recount",
        created_at=datetime(2026, 9, 10, 10, 0, 0),
    ))
    db.add(models.RawMaterialStockMovement(
        raw_material_id=9999, movement_type="adjustment",
        quantity=Decimal("-1"), unit="kg", notes="gone master",
        created_at=datetime(2026, 9, 11, 10, 0, 0),
    ))
    prod = models.Product(product_id="P-P3", name="P3 Masala")
    db.add(prod)
    db.flush()
    size = models.PackagingSize(
        product_id=prod.id, size_name="250g", weight_in_kg=Decimal("0.25"),
        rate_per_kg=Decimal("200"), packets_per_unit=1, is_active=True,
    )
    db.add(size)
    db.flush()
    db.add(models.FinishedProductStockMovement(
        product_id=prod.id, packaging_size_id=size.id,
        movement_type=FinishedProductMovementType.ADJUSTMENT,
        quantity=Decimal("5"), notes="found",
        created_at=datetime(2026, 9, 12, 10, 0, 0),
    ))
    db.add(models.FinishedProductStockMovement(
        product_id=8888, packaging_size_id=7777,
        movement_type=FinishedProductMovementType.ADJUSTMENT,
        quantity=Decimal("1"), notes="gone masters",
        created_at=datetime(2026, 9, 9, 10, 0, 0),
    ))
    db.add(models.FinishedProductStockMovement(
        product_id=prod.id, packaging_size_id=size.id,
        movement_type=FinishedProductMovementType.PRODUCTION,
        quantity=Decimal("100"), notes="batch",
        created_at=datetime(2026, 9, 13, 10, 0, 0),
    ))
    db.commit()
    return mat, prod, size


def test_adjustment_list_output_unchanged(db):
    _seed_adjustments(db)
    rows = m.list_stock_adjustments(db)
    assert [(r["id"].split("-")[0], r["item"], r["quantity"]) for r in rows] == [
        ("fin", "P3 Masala (250g)", 5.0),
        ("raw", "#9999", -1.0),
        ("raw", "P3 Spice", 2.0),
        ("fin", "#8888", 1.0),
    ]
    assert [r["kind"] for r in rows] == [
        "Finished Goods", "Raw Material", "Raw Material", "Finished Goods",
    ]


def test_theme_toggle_and_dark_overrides():
    index = _read("frontend/index.html")
    assert "theme-toggle" in index, "no header theme toggle"
    css = _read("frontend/css/style.css")
    assert '[data-theme="dark"]' in css, "no dark overrides"


def test_theme_pref_defaults_light(db):
    row = models.DashboardPreference(user_id=1)
    db.add(row)
    db.commit()
    assert row.theme == "light"
    main_py = _read("backend/main.py")
    assert '"theme"' in main_py, "prefs endpoints ignore theme"
    db_py = _read("backend/app/core/database.py")
    assert "dashboard_preferences" in db_py and '"theme"' in db_py, (
        "no migration for existing databases"
    )


def test_low_spec_mode_toggle_and_effects():
    dash = _read("frontend/pages/dashboard.html")
    assert "perf-low-spec" in dash, "no low-spec toggle in Customize"
    assert "dash_low_spec" in dash, "low-spec choice is not persisted"
    app_js = _read("frontend/js/app.js")
    assert "dash_low_spec" in app_js, "refresh interval ignores low-spec mode"
    assert "300000" in app_js, "no stretched interval"
    comp = _read("frontend/js/components.js")
    assert "dash_low_spec" in comp, "page sizes ignore low-spec mode"
