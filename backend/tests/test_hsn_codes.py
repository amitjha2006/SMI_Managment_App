"""HSN picker lists and HSN import columns.

Products keep their free-text HSN field but gain a curated picker; raw
materials gain an HSN field of their own. Both import templates carry an
"HSN Code" column that lands in the same field. These tests pin:

* GET /hsn-codes serves the product list (17) and the raw-material list
* Excel shapes (int/float/str/blank/garbage) normalise to digits or blank
* import stores the column on create, tops up a blank product HSN on
  re-import, and never overwrites a code already set

Run: python -m pytest backend/tests/test_hsn_codes.py -v
"""

from io import BytesIO

from openpyxl import Workbook

from backend.app.services.excel_import import run_import
from backend.app.services.hsn_codes import normalize_hsn, suggest_hsn_code


def _xlsx(headers, rows):
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


def test_picker_lists_have_expected_counts():
    from backend.main import list_hsn_codes

    everything = list_hsn_codes()
    assert len(everything["products"]) == 23, len(everything["products"])
    assert len(everything["raw_materials"]) == 54, len(everything["raw_materials"])
    assert {e["code"] for e in everything["products"]} >= {"09109100", "09109990", "21039090"}
    only_products = list_hsn_codes("product")
    assert [e["code"] for e in only_products["codes"]] == [
        e["code"] for e in everything["products"]
    ]
    only_raw = list_hsn_codes("raw_material")
    assert len(only_raw["codes"]) == 54


def test_picker_lists_stay_alphabetical():
    from backend.app.services.hsn_codes import PRODUCT_HSN, RAW_MATERIAL_HSN

    for label, lst in (("products", PRODUCT_HSN), ("raw_materials", RAW_MATERIAL_HSN)):
        names = [e["name"] for e in lst]
        assert names == sorted(names, key=str.lower), f"{label} picker not alphabetical"


def test_normalize_hsn_shapes():
    assert normalize_hsn("09109100") == "09109100"
    assert normalize_hsn(" 09092200 ") == "09092200"
    assert normalize_hsn(9103020) == "9103020"
    assert normalize_hsn(9103020.0) == "9103020"
    assert normalize_hsn("") is None
    assert normalize_hsn(None) is None
    assert normalize_hsn("0904 - Pepper") is None
    assert normalize_hsn("09AB2200") is None


def test_raw_import_stores_hsn(db):
    from backend.app.models import RawMaterial

    summary = run_import(
        db,
        "raw_materials",
        _xlsx(
            ["Raw Material Name", "Unit", "HSN Code"],
            [["Guntur Chilli", "kg", "09042211"]],
        ),
    )
    assert summary["imported"] == 1, summary
    mat = db.query(RawMaterial).filter(RawMaterial.name == "Guntur Chilli").one()
    assert mat.hsn_code == "09042211"


def test_raw_import_garbage_hsn_stays_blank(db):
    from backend.app.models import RawMaterial

    summary = run_import(
        db,
        "raw_materials",
        _xlsx(
            ["Raw Material Name", "Unit", "HSN Code"],
            [["Bad Code Chilli", "kg", "not-a-code"]],
        ),
    )
    assert summary["imported"] == 1, summary
    mat = db.query(RawMaterial).filter(RawMaterial.name == "Bad Code Chilli").one()
    assert mat.hsn_code is None


def test_product_import_stores_and_tops_up_hsn(db):
    from backend.app.models import Product

    first = run_import(
        db,
        "products",
        _xlsx(
            ["Product ID", "Product Name", "Packaging Size", "HSN Code"],
            [["PROD-HSN-1", "HSN Masala", "100g", "09109100"]],
        ),
    )
    assert first["imported"] == 1, first
    prod = db.query(Product).filter(Product.name == "HSN Masala").one()
    assert prod.hsn_code == "09109100"

    # A re-imported row for a second size keeps the code already set.
    second = run_import(
        db,
        "products",
        _xlsx(
            ["Product ID", "Product Name", "Packaging Size", "HSN Code"],
            [["PROD-HSN-1", "HSN Masala", "250g", "09109990"]],
        ),
    )
    assert second["imported"] == 1, second
    db.refresh(prod)
    assert prod.hsn_code == "09109100"

    # ...while a product imported without one picks it up on re-import.
    run_import(
        db,
        "products",
        _xlsx(
            ["Product ID", "Product Name", "Packaging Size"],
            [["PROD-HSN-2", "Blank Masala", "100g"]],
        ),
    )
    blank = db.query(Product).filter(Product.name == "Blank Masala").one()
    assert blank.hsn_code is None
    run_import(
        db,
        "products",
        _xlsx(
            ["Product ID", "Product Name", "Packaging Size", "HSN Code"],
            [["PROD-HSN-2", "Blank Masala", "250g", "09105000"]],
        ),
    )
    db.refresh(blank)
    assert blank.hsn_code == "09105000"


def test_suggest_blend_words_win_for_products():
    assert suggest_hsn_code("Shresth Chilli Masala", "product")["code"] == "09109990"
    assert suggest_hsn_code("Turmeric Powder", "product")["code"] == "09103030"
    assert suggest_hsn_code("Jeera Powder", "product")["code"] == "09093200"
    assert suggest_hsn_code("Garam Masala", "product")["code"] == "21039090"
    assert suggest_hsn_code("Chaat Masala", "product")["code"] == "09109100"


def test_suggest_raw_defaults_to_seed():
    assert suggest_hsn_code("Cumin", "raw_material")["code"] == "09093129"
    assert suggest_hsn_code("Cumin Powder", "raw_material")["code"] == "09093200"
    assert suggest_hsn_code("Saunf", "raw_material")["code"] == "09096119"
    assert suggest_hsn_code("Guntur Chilli", "raw_material")["code"] == "9042110"
    assert suggest_hsn_code("Badi Elaichi", "raw_material")["code"] == "9083110"
    assert suggest_hsn_code("Black Cardamom (बड़ी इलाइची)", "raw_material")["code"] == "9083110"
    assert suggest_hsn_code("Pouch label Turmeric", "raw_material")["code"] == "39199090"


def test_suggest_no_match_is_none():
    assert suggest_hsn_code("Factory Sweepings", "product") is None
    assert suggest_hsn_code("", "raw_material") is None
    assert suggest_hsn_code(None, "product") is None


def test_suggest_endpoint_returns_code_or_null():
    from backend.main import suggest_hsn_code as suggest_route

    assert suggest_route("product", "Haldi Powder")["code"] == "09103030"
    assert suggest_route("raw_material", "no such spice")["code"] is None


def test_export_import_round_trip_keeps_hsn(make_db):
    from backend.app.models import Product, RawMaterial
    from backend.app.services.excel_export import run_export

    db1, db2 = make_db(), make_db()
    assert run_import(
        db1,
        "raw_materials",
        _xlsx(
            ["Raw Material Name", "Unit", "HSN Code"],
            [["Round Trip Chilli", "kg", "09042110"]],
        ),
    )["imported"] == 1
    assert run_import(
        db1,
        "products",
        _xlsx(
            ["Product ID", "Product Name", "Packaging Size", "HSN Code"],
            [["PROD-RT-1", "Round Trip Masala", "100g", "09109990"]],
        ),
    )["imported"] == 1

    assert run_import(db2, "raw_materials", run_export(db1, "raw_materials")["content"])[
        "imported"
    ] == 1
    assert run_import(db2, "products", run_export(db1, "products")["content"])[
        "imported"
    ] == 1

    mat = db2.query(RawMaterial).filter(RawMaterial.name == "Round Trip Chilli").one()
    assert mat.hsn_code == "09042110"
    prod = db2.query(Product).filter(Product.name == "Round Trip Masala").one()
    assert prod.hsn_code == "09109990"
