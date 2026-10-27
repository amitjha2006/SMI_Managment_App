"""List endpoints must survive legacy rows with an empty contact number.

The importer stores "" when only a name is given, but the response schemas
demanded min_length=5. One such row then 500'd the ENTIRE list (FastAPI
serializes the whole response as one unit), showing "Failed to load" and
hiding every other row. Entry-time validation (Create schemas) stays strict;
only the read schemas tolerate what is already in the database.
"""

import pytest
from pydantic import ValidationError

from backend.app.models import Customer, Supplier
from backend.app.schemas import CustomerSchema, SupplierCreate, SupplierSchema


def test_supplier_response_accepts_empty_contact(db):
    db.add(Supplier(supplier_id="SUP-1", name="Name Only", contact_number=""))
    db.commit()
    row = db.query(Supplier).first()
    data = SupplierSchema.model_validate(row).model_dump()
    assert data["contact_number"] == ""


def test_customer_response_accepts_empty_contact(db):
    db.add(Customer(customer_id="CUST-1", name="Name Only", contact_number=""))
    db.commit()
    row = db.query(Customer).first()
    data = CustomerSchema.model_validate(row).model_dump()
    assert data["contact_number"] == ""


def test_create_still_requires_contact():
    with pytest.raises(ValidationError):
        SupplierCreate(name="No Contact", contact_number="123")
