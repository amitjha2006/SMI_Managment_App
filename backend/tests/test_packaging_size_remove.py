"""Removing an unwanted pack size from a product.

Hard-deleting is unsafe: challan items, production entries and stock
movements point at the size by id, and issued documents must keep reading
their frozen history. So removal deactivates (is_active=False, like products
and customers); history keeps resolving by id while dropdowns stop offering
the size. The last active size cannot go (a product with no usable size
breaks challan and production creation), and re-adding the same name
restores the row instead of duplicating it.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.app.core.security import create_access_token


def _mkuser(db, username="size_admin"):
    from backend.app.models.user import User

    u = User(username=username, hashed_password="x", role="admin", is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _req(username):
    return SimpleNamespace(query_params={"reason": "test change note"}, 
        headers={
            "Authorization": "Bearer "
            + create_access_token(data={"sub": username, "editor": True})
        },
        client=None,
    )


def _product(db, name="Chilli"):
    from backend.app.models import Product

    p = Product(product_id=f"P-{name}", name=name, is_active=True)
    db.add(p)
    db.flush()
    return p


def _size(db, product_id, name, weight=0.05):
    from backend.app.models import PackagingSize

    s = PackagingSize(
        product_id=product_id, size_name=name, weight_in_kg=weight, packets_per_unit=1
    )
    db.add(s)
    db.flush()
    return s


def test_remove_deactivates_and_audits_but_keeps_the_row(db):
    from backend.app.models import AuditLog, PackagingSize
    from backend.main import delete_packaging_size

    owner = _mkuser(db, "size_admin1")
    p = _product(db, "Chilli1")
    s1 = _size(db, p.id, "50g")
    _size(db, p.id, "100g", 0.1)

    out = delete_packaging_size(s1.id, _req("size_admin1"), db)
    assert out["status"] == "deleted"

    kept = db.query(PackagingSize).filter(PackagingSize.id == s1.id).one()
    assert kept.is_active is False
    assert db.query(PackagingSize).filter(PackagingSize.product_id == p.id).count() == 2
    row = (
        db.query(AuditLog)
        .filter(AuditLog.action == "delete", AuditLog.module == "masters")
        .first()
    )
    assert row is not None and row.user_id == owner.id


def test_last_active_size_is_refused(db):
    from backend.main import delete_packaging_size

    _mkuser(db, "size_admin2")
    p = _product(db, "Chilli2")
    only = _size(db, p.id, "50g")

    with pytest.raises(HTTPException) as e:
        delete_packaging_size(only.id, _req("size_admin2"), db)
    assert e.value.status_code == 409


def test_missing_size_is_404(db):
    from backend.main import delete_packaging_size

    _mkuser(db, "size_admin3")
    with pytest.raises(HTTPException) as e:
        delete_packaging_size(999999, _req("size_admin3"), db)
    assert e.value.status_code == 404


def test_readding_a_removed_name_restores_it(db):
    from backend.app.models import PackagingSize
    from backend.main import _attach_sizes_to_product, delete_packaging_size

    _mkuser(db, "size_admin4")
    p = _product(db, "Chilli4")
    s1 = _size(db, p.id, "50g")
    _size(db, p.id, "100g", 0.1)
    delete_packaging_size(s1.id, _req("size_admin4"), db)

    added, skipped = _attach_sizes_to_product(
        db, p, [{"size_name": "50g", "weight_in_kg": 0.05, "rate_per_kg": 10}]
    )
    assert added == ["50g"] and skipped == []
    assert db.query(PackagingSize).filter(PackagingSize.product_id == p.id).count() == 2
    assert (
        db.query(PackagingSize).filter(PackagingSize.id == s1.id).one().is_active is True
    )


def test_editor_offers_remove_and_hides_inactive():
    """The Edit panel must have a Remove button per size, and every picker
    must skip inactive sizes - otherwise removal exists server-side but the
    user can neither trigger it nor see its effect."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent.parent
    pages = root / "frontend" / "pages"
    inv = (pages / "inventory.html").read_text(encoding="utf-8")
    assert "removeSize(" in inv
    assert '"/packaging-sizes/" + sizeId, { method: "DELETE" }' in inv
    # activeSizes() helper (defined once) applied at every size listing.
    assert inv.count("activeSizes(") >= 6
    chal = (pages / "challans.html").read_text(encoding="utf-8")
    assert "is_active !== false" in chal
    prod = (pages / "production.html").read_text(encoding="utf-8")
    assert "is_active !== false" in prod


def test_new_documents_refuse_a_removed_size(db):
    from backend.app.models import Customer
    from backend.main import create_challan, delete_packaging_size

    _mkuser(db, "size_admin5")
    p = _product(db, "Chilli5")
    s1 = _size(db, p.id, "50g")
    _size(db, p.id, "100g", 0.1)
    db.add(Customer(customer_id="C-5", name="Buyer", contact_number="9876543210"))
    db.commit()
    customer = db.query(Customer).first()

    delete_packaging_size(s1.id, _req("size_admin5"), db)
    with pytest.raises(HTTPException) as e:
        create_challan(
            {
                "customer_id": customer.id,
                "items": [{"product_id": p.id, "packaging_size_id": s1.id, "quantity": 1}],
            },
            _req("size_admin5"),
            db,
        )
    assert e.value.status_code == 400


def test_import_reactivates_a_removed_size(db):
    """Re-importing a removed size must bring it back, like re-adding by
    hand does. Otherwise the import reports success while the size stays
    hidden from every picker."""
    from io import BytesIO

    from openpyxl import Workbook

    from backend.app.models import PackagingSize
    from backend.app.services.excel_import import run_import
    from backend.main import delete_packaging_size

    _mkuser(db, "size_admin6")
    p = _product(db, "Chilli6")
    s1 = _size(db, p.id, "50g")
    _size(db, p.id, "100g", 0.1)
    delete_packaging_size(s1.id, _req("size_admin6"), db)
    assert db.query(PackagingSize).filter(PackagingSize.id == s1.id).one().is_active is False

    wb = Workbook()
    ws = wb.active
    ws.append(["Product Name", "Packaging Size", "Weight (kg)"])
    ws.append(["Chilli6", "50g", 0.05])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    summary = run_import(db, "products", buf.read())
    assert summary["imported"] == 1, summary
    assert db.query(PackagingSize).filter(PackagingSize.id == s1.id).one().is_active is True
    assert (
        db.query(PackagingSize).filter(PackagingSize.product_id == p.id).count() == 2
    )


def test_import_matches_size_names_case_insensitively(db):
    """The product-create path matches sizes case-insensitively; the
    importer must too, or '50G' duplicates an existing '50g'."""
    from io import BytesIO

    from openpyxl import Workbook

    from backend.app.models import PackagingSize
    from backend.app.services.excel_import import run_import

    p = _product(db, "Chilli7")
    _size(db, p.id, "50g")

    wb = Workbook()
    ws = wb.active
    ws.append(["Product Name", "Packaging Size", "Weight (kg)"])
    ws.append(["Chilli7", "50G", 0.05])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    summary = run_import(db, "products", buf.read())
    assert summary["imported"] == 1, summary
    assert (
        db.query(PackagingSize).filter(PackagingSize.product_id == p.id).count() == 1
    )


def test_remove_button_passes_no_name():
    """The Remove button must not interpolate the size name into the
    onclick attribute: a quote in the name breaks out of the JS string
    and kills the button. The handler reads the name from the row input."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent.parent
    inv = (root / "frontend" / "pages" / "inventory.html").read_text(encoding="utf-8")
    assert "onclick=\"removeSize(${ps.id})\"" in inv
    assert 'se-sn-" + sizeId' in inv
