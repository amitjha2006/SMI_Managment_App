"""Product imports survive duplicates; failures still leave a report.

A duplicate product_id used to kill the whole batch with a 500 and leave
no report behind. Run: python -m pytest backend/tests/test_import_row_resilience.py -v
"""

import asyncio
import io
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import backend.main as m  # noqa: E402
from backend.app import models  # noqa: E402


def _req():
    return SimpleNamespace(headers={}, client=None)


def _products_xlsx(rows):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Product ID", "Product Name", "Packaging Size"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_generated_id_skips_taken_ids(db):
    db.add(models.Product(product_id="PROD-1001", name="Old One"))
    db.commit()
    out = m.run_import(db, "products", _products_xlsx([
        ["", "Brand New Masala", "100g"],
    ]))
    assert out["failed"] == 0 and out["imported"] == 1, out
    got = db.query(models.Product).filter(
        models.Product.name == "Brand New Masala").one()
    assert got.product_id == "PROD-1002", got.product_id


def test_flush_collision_becomes_skip_not_crash(db, monkeypatch):
    # A duplicate invisible to the pre-checks (concurrent double submit):
    # the row's flush raises, the batch must survive as a skip.
    real_flush = db.flush
    calls = []

    def flaky_flush(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise IntegrityError("INSERT INTO products", {}, Exception("UNIQUE failed"))
        return real_flush(*args, **kwargs)

    monkeypatch.setattr(db, "flush", flaky_flush)
    out = m.run_import(db, "products", _products_xlsx([
        ["PROD-FL1", "Flaky Masala", "100g"],
        ["PROD-FL2", "Fine Masala", "50g"],
    ]))
    assert out["failed"] == 0, out
    assert out["imported"] == 1 and out["skipped"] == 1, out
    reasons = " ".join(r["reason"] for r in out["skipped_rows"])
    assert "already exists" in reasons, out["skipped_rows"]


def test_failed_import_still_saves_report(db, monkeypatch):
    def fake_backup(*_args, **_kwargs):
        return SimpleNamespace(id=98, filename="pre-import_test.db")

    def boom(_db, _import_type, _content, _sheet=None):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(m, "_do_backup", fake_backup)
    monkeypatch.setattr(m, "run_import", boom)

    async def fake_read():
        return b"fake"

    with pytest.raises(HTTPException) as exc:
        asyncio.run(m.import_data(
            "products", _req(),
            SimpleNamespace(filename="p.xlsx", read=fake_read), None, db))
    assert exc.value.status_code == 500
    rows = m.list_import_reports(db=db)
    assert len(rows) == 1, rows
    rep = rows[0]
    assert rep["failed"] == 1 and rep["imported"] == 0, rep
    assert "synthetic failure" in rep["errors"][0]["message"], rep["errors"]


def test_import_button_guards_double_submit():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "import.html"), encoding="utf-8") as fh:
        html = fh.read()
    assert "onclick=\"runImport(event)\"" in html
    assert "submitGuard(event)" in html


def _challans_xlsx(rows):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Challan Number", "Challan Date", "Customer Name",
               "Product Name", "Packaging Size", "Quantity", "Rate per Kg"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_challan_import_freezes_default_footer(db):
    # text is frozen at import, like the create route does.
    db.add(models.PrintFooter(name="Detailed", body="Footer text.", is_active=True))
    db.commit()
    foot = db.query(models.PrintFooter).filter(
        models.PrintFooter.name == "Detailed").one()
    db.add(models.ApplicationSetting(key="sales.footer_id", value=str(foot.id)))
    db.commit()
    db.add(models.Customer(customer_id="CUST-CHF", name="Footer Co",
                           contact_number="9000000093"))
    db.commit()
    out = m.run_import(db, "sales_challans", _challans_xlsx([
        ["CH-FT-1", "2025-04-02", "Footer Co", "Foot Masala", "250g", 10, 200],
    ]))
    assert out["failed"] == 0 and out["imported"] == 1, out
    got = db.query(models.SalesChallan).filter(
        models.SalesChallan.challan_number == "CH-FT-1").one()
    assert got.footer_id == foot.id, got.footer_id
    assert got.company_terms == "Footer text.", got.company_terms


def test_challan_import_gives_each_new_product_its_own_id(db):
    # Regression: the generated id reused len(rows) verbatim, so the second
    # new product in one file died on UNIQUE (PROD-11040) and killed the batch.
    db.add(models.Customer(customer_id="CUST-CH1", name="Challan Co",
                           contact_number="9000000092"))
    db.commit()
    out = m.run_import(db, "sales_challans", _challans_xlsx([
        ["CH-NEW-1", "2025-04-02", "Challan Co", "Brand New Masala A", "250g", 10, 200],
        ["CH-NEW-1", "2025-04-02", "Challan Co", "Brand New Masala B", "250g", 5, 150],
        ["CH-NEW-2", "2025-04-03", "Challan Co", "Brand New Masala C", "100g", 8, 240],
    ]))
    assert out["failed"] == 0, out
    assert out["imported"] == 2, out
    ids = [p.product_id for p in db.query(models.Product).filter(
        models.Product.name.like("Brand New Masala%")).all()]
    assert len(ids) == 3 and len(set(ids)) == 3, ids


def test_product_import_merges_case_and_space_variants(db):
    # The product form normalises names (case, repeated/non-breaking
    # spaces, trailing weight token); the importer compared ==, so "cumin Powder" sailed
    # past "Cumin Powder" and duplicated it.
    db.add(models.Product(product_id="PROD-2001", name="Cumin Powder"))
    db.commit()
    out = m.run_import(db, "products", _products_xlsx([
        ["", "cumin  Powder", "100g"],
    ]))
    assert out["failed"] == 0 and out["imported"] == 1, out
    got = db.query(models.Product).filter(
        models.Product.name.like("%umin%owder")).all()
    assert len(got) == 1 and got[0].product_id == "PROD-2001", got


def test_challan_import_finds_product_despite_case(db):
    db.add(models.Customer(customer_id="CUST-CHC", name="Case Co",
                           contact_number="9000000094"))
    db.add(models.Product(product_id="PROD-2002", name="Chilli Masala"))
    db.commit()
    out = m.run_import(db, "sales_challans", _challans_xlsx([
        ["CH-CS-1", "2025-04-02", "Case Co", "CHILLI  MASALA", "250g", 10, 200],
    ]))
    assert out["failed"] == 0 and out["imported"] == 1, out
    assert db.query(models.Product).count() == 1, "duplicate product created"


def test_loose_size_names_merge_into_loose_powder(db):
    db.add(models.Product(product_id="PROD-2003", name="Loose Test Masala"))
    db.commit()
    out = m.run_import(db, "products", _products_xlsx([
        ["", "Loose Test Masala", "loose"],
        ["", "Loose Test Masala", "Loose Masala"],
    ]))
    assert out["failed"] == 0 and out["imported"] == 2, out
    sizes = db.query(models.PackagingSize).all()
    assert [s.size_name for s in sizes] == ["Loose Powder"], [s.size_name for s in sizes]


def test_attach_sizes_merges_loose_spellings(db):
    prod = models.Product(product_id="PROD-2004", name="Attach Masala")
    db.add(prod)
    db.commit()
    added, skipped = m._attach_sizes_to_product(db, prod, [
        {"size_name": "loose", "weight_in_kg": 1},
        {"size_name": "Loose Masala", "weight_in_kg": 1},
    ])
    assert added == ["Loose Powder"], (added, skipped)
    assert skipped == ["Loose Powder"], (added, skipped)
