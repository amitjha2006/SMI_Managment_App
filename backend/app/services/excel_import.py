"""Excel import service for the Shresth Masala Industry Management System.

This module handles importing legacy data from Excel files into the database.
Supported import types:
- customers
- suppliers
- raw_materials
- products + packaging sizes
- purchase_invoices
- sales_challans (+ payments)
- expenses
- employees
"""

import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.database import _ist_wall
from backend.app.models import (
    Customer,
    CustomerPayment,
    Employee,
    Expense,
    ExpenseCategory,
    PackagingSize,
    Product,
    ProductCategory,
    PurchaseInvoice,
    PurchaseInvoiceItem,
    RawMaterial,
    RawMaterialCategory,
    SalesChallan,
    SalesChallanItem,
    Supplier,
)
from backend.app.services.challan_snapshot import apply_snapshot, company_profile, default_footer
from backend.app.services.hsn_codes import normalize_hsn
from backend.app.services.stock_posting import post_challan_sales_movements


class ImportResult:
    """Structured result of an import operation."""

    def __init__(self, import_type: str):
        self.import_type = import_type
        self.total_rows: int = 0
        self.imported: int = 0
        self.skipped: int = 0
        self.failed: int = 0
        self.errors: list[dict[str, Any]] = []
        self.skipped_rows: list[dict[str, Any]] = []

    def add_error(self, row_num: int, message: str, data: dict | None = None):
        self.failed += 1
        self.errors.append({"row": row_num, "message": str(message), "data": data or {}})

    def add_skipped(self, row_num: int, reason: str, data: dict | None = None):
        self.skipped += 1
        self.skipped_rows.append({"row": row_num, "reason": reason, "data": data or {}})

    def summary(self) -> dict[str, Any]:
        # An import that skipped every row imported nothing: reporting
        # success then hides the problem (the branch-PC symptom). Callers
        # that need "nothing crashed" can check failed == 0 instead.
        all_skipped = self.total_rows > 0 and self.imported == 0 and self.failed == 0
        return {
            "import_type": self.import_type,
            "total_rows": self.total_rows,
            "imported": self.imported,
            "skipped": self.skipped,
            "failed": self.failed,
            "success": self.imported > 0,
            "all_skipped": all_skipped,
            "warning": None,
            "errors": self.errors[:50],  # cap error detail in response
            "skipped_rows": self.skipped_rows[:50],
        }


# Excel keeps dates as day counts since 1899-12-30. When a sheet's date
# column is not formatted as a date, the cell reaches openpyxl as a bare
# number (46238) or a numeric string - none of the text formats can read
# it, and imports silently fell back to "now". Challans 270/1726/273 were
# imported exactly that way (sheet held 46238/46256/46265). Unformatted
# date cells arrive as whole numbers, so only whole numbers in a sane
# factory-era window decode; quantities, ids and time-fractions must
# never become dates.
_EXCEL_EPOCH = datetime(1899, 12, 30)
_EXCEL_SERIAL_MIN = 20000  # 1954-10-16: no masala-factory row predates this
_EXCEL_SERIAL_MAX = 80000  # 2112: an id/qty this size is not a date either


def _excel_serial(value: Any) -> datetime | None:
    """Decode an Excel date serial (whole number, sane range) to midnight."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    serial = float(value)
    if not serial.is_integer() or not (_EXCEL_SERIAL_MIN <= serial <= _EXCEL_SERIAL_MAX):
        return None
    return _EXCEL_EPOCH + timedelta(days=int(serial))


def _date_from_text(text: str) -> datetime | None:
    """String dates: numeric serials first, then the accepted formats.

    Ambiguous slash dates (8/4/2026) resolve month-first: the shop's own
    sheets use m/d/yyyy, and the shipped templates carry real date cells,
    so typed text dates are rare.
    """
    if re.fullmatch(r"\d{4,7}", text):
        # Numeric strings take the same serial decode path; only whole
        # numbers in range survive.
        return _excel_serial(float(text))
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%d.%m.%Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def parse_date(value: Any) -> datetime | None:
    """Parse a date/datetime from various Excel cell formats."""
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    # Unformatted date cells: bare Excel serials decode to real dates.
    serial = _excel_serial(value)
    if serial is not None:
        return serial
    if isinstance(value, (int, float)):
        return None
    return _date_from_text(str(value).strip())


def parse_decimal(value: Any) -> Decimal | None:
    """Parse a decimal value, stripping currency symbols and commas."""
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip().replace(",", "").replace("₹", "").replace("Rs ", "").replace("+", "")
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def parse_number(value: Any) -> Decimal | None:
    """Parse a plain numeric value."""
    return parse_decimal(value)


def normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def normalize_lower(value: Any) -> str | None:
    text = normalize_text(value)
    return text.lower() if text else None


def parse_payment_mode(value: Any) -> str:
    modes = {
        "cash": "cash",
        "cash payment": "cash",
        "upi": "upi",
        "google pay": "upi",
        "gpay": "upi",
        "phonepe": "upi",
        "paytm": "upi",
        "bank transfer": "bank_transfer",
        "bank transfer (bt)": "bank_transfer",
        "bt": "bank_transfer",
        "neft": "bank_transfer",
        "rtgs": "bank_transfer",
        "imps": "bank_transfer",
        "cheque": "cheque",
        "check": "cheque",
        "card": "card",
        "credit card": "card",
        "debit card": "card",
        "other": "other",
    }
    text = normalize_lower(value)
    if text and text in modes:
        return modes[text]
    # fuzzy match
    if text:
        for key, mapped in modes.items():
            if key in text:
                return mapped
    return "other"


def _get_or_create_category(db: Session, name: str) -> ExpenseCategory | None:
    if not name:
        return None
    category = db.query(ExpenseCategory).filter(ExpenseCategory.name == name).first()
    if not category:
        category = ExpenseCategory(name=name, is_active=True)
        db.add(category)
        db.flush()
    return category


def _get_or_create_product_category(db: Session, name: str):
    """Find or create a product category by name (case-insensitive).

    Products moved from free-text `category` to `category_id`; the importer
    still speaks names, so it resolves them here instead of assigning to the
    now read-only `category` property.
    """
    if not name:
        return None
    category = (
        db.query(ProductCategory).filter(ProductCategory.name.ilike(name)).first()
    )
    if not category:
        category = ProductCategory(name=name, is_active=True)
        db.add(category)
        db.flush()
    return category


def _get_or_create_raw_material_category(db: Session, name: str) -> RawMaterialCategory | None:
    if not name:
        return None
    category = db.query(RawMaterialCategory).filter(RawMaterialCategory.name == name).first()
    if not category:
        category = RawMaterialCategory(name=name)
        db.add(category)
        db.flush()
    return category


def _find_customer(db: Session, row: dict[str, Any]) -> Customer | None:
    """Find a customer by name, contact number or customer_id."""
    customer_id = normalize_text(row.get("Customer ID") or row.get("customer_id"))
    if customer_id:
        c = db.query(Customer).filter(Customer.customer_id == customer_id).first()
        if c:
            return c
    name = normalize_text(row.get("Customer Name") or row.get("customer_name") or row.get("Name"))
    if name:
        c = db.query(Customer).filter(Customer.name == name).first()
        if c:
            return c
    phone = normalize_text(row.get("Contact Number") or row.get("Contact"))
    if phone:
        c = db.query(Customer).filter(Customer.contact_number == phone).first()
        if c:
            return c
    return None


def _find_supplier(db: Session, row: dict[str, Any]) -> Supplier | None:
    """Find a supplier by ID, name, company name or contact number."""
    supplier_id = normalize_text(row.get("Supplier ID") or row.get("supplier_id"))
    if supplier_id:
        s = db.query(Supplier).filter(Supplier.supplier_id == supplier_id).first()
        if s:
            return s
    name = normalize_text(row.get("Supplier Name") or row.get("supplier_name") or row.get("Name"))
    if name:
        s = db.query(Supplier).filter(Supplier.name == name).first()
        if s:
            return s
    company = normalize_text(row.get("Company Name") or row.get("Company") or name)
    if company:
        s = db.query(Supplier).filter(Supplier.company_name == company).first()
        if s:
            return s
    phone = normalize_text(row.get("Contact Number") or row.get("Contact"))
    if phone:
        s = db.query(Supplier).filter(Supplier.contact_number == phone).first()
        if s:
            return s
    return None


def _find_raw_material(db: Session, row: dict[str, Any]) -> RawMaterial | None:
    material_id = normalize_text(
        row.get("Material ID") or row.get("Raw Material ID") or row.get("material_id")
    )
    if material_id:
        m = db.query(RawMaterial).filter(RawMaterial.material_id == material_id).first()
        if m:
            return m
    name = normalize_text(
        row.get("Raw Material Name") or row.get("Raw Material") or row.get("Name")
    )
    if name:
        m = db.query(RawMaterial).filter(RawMaterial.name == name).first()
        if m:
            return m
    return None


def _find_product(db: Session, row: dict[str, Any]) -> Product | None:
    product_id = normalize_text(row.get("Product ID") or row.get("product_id"))
    if product_id:
        p = db.query(Product).filter(Product.product_id == product_id).first()
        if p:
            return p
    name = normalize_text(row.get("Product Name") or row.get("Product") or row.get("Name"))
    if name:
        p = db.query(Product).filter(Product.name == name).first()
        if p:
            return p
        return _match_product_by_name(db, name)
    return None


_PRODUCT_NAME_WEIGHT_TAIL = re.compile(r"\s*\d+(?:[.,]\d+)?\s*(?:g|gm|grams|kgs|kg)\s*$", re.I)


def _normalised_product_name(name):
    """Same identity rule as the product form: case, repeated spaces and a
    trailing weight token are ignored, so "cumin Powder" resolves to
    "Cumin Powder" instead of duplicating it."""
    n = re.sub(r"\s+", " ", (name or "").strip())
    n = _PRODUCT_NAME_WEIGHT_TAIL.sub("", n).strip()
    return n.casefold()


def _match_product_by_name(db: Session, name):
    """Normalized name match over the (small) products table."""
    want = _normalised_product_name(name)
    if not want:
        return None
    for p in db.query(Product).all():
        if _normalised_product_name(p.name) == want:
            return p
    return None


# ---------------------------------------------------------------------------
# Importers
# ---------------------------------------------------------------------------


def import_customers(
    db: Session, rows: list[dict[str, Any]], import_type: str = "customers"
) -> ImportResult:
    result = ImportResult(import_type)
    result.total_rows = len(rows)

    for idx, row in enumerate(rows):
        row_num = idx + 2  # skip header row
        name = normalize_text(row.get("Customer Name") or row.get("Name"))
        contact = normalize_text(row.get("Contact Number") or row.get("Contact"))
        if not name and not contact:
            result.add_skipped(row_num, "Missing name and contact", row)
            continue

        existing = _find_customer(db, row)
        if existing:
            # Update balance fields if provided
            opening = parse_decimal(row.get("Opening Balance"))
            if opening is not None:
                existing.opening_balance = opening
                existing.outstanding_balance = opening
            else:
                result.add_skipped(row_num, "Customer already exists", row)
                continue
            result.imported += 1
            continue

        customer_id = normalize_text(row.get("Customer ID"))
        if not customer_id:
            customer_id = f"CUST-{1000 + result.imported + 1:04d}"

        customer = Customer(
            customer_id=customer_id,
            name=name or contact or "Unknown",
            company_name=normalize_text(row.get("Company Name") or row.get("Company")),
            gst_no=normalize_text(row.get("GST No") or row.get("GSTIN")),
            pan_no=normalize_text(row.get("PAN No") or row.get("PAN")),
            contact_number=contact or "",
            contact_number_2=normalize_text(row.get("Contact Number 2") or row.get("Contact 2")),
            email=normalize_text(row.get("Email")),
            address=normalize_text(row.get("Address")),
            account_no=normalize_text(row.get("Account No") or row.get("Bank Account")),
            ifsc_code=normalize_text(row.get("IFSC Code") or row.get("IFSC")),
            bank_detail=normalize_text(row.get("Bank Detail") or row.get("Bank Name")),
            cash=parse_decimal(row.get("Cash")) or Decimal("0"),
            notes=normalize_text(row.get("Notes")),
            opening_balance=parse_decimal(row.get("Opening Balance")) or Decimal("0"),
            outstanding_balance=parse_decimal(row.get("Outstanding Balance")) or Decimal("0"),
            credit_limit=parse_decimal(row.get("Credit Limit")) or Decimal("0"),
            is_active=True,
        )
        db.add(customer)
        result.imported += 1

    db.commit()
    return result


def import_suppliers(
    db: Session, rows: list[dict[str, Any]], import_type: str = "suppliers"
) -> ImportResult:
    result = ImportResult(import_type)
    result.total_rows = len(rows)

    for idx, row in enumerate(rows):
        row_num = idx + 2
        name = normalize_text(row.get("Supplier Name") or row.get("Name"))
        contact = normalize_text(row.get("Contact Number") or row.get("Contact"))
        if not name and not contact:
            result.add_skipped(row_num, "Missing name and contact", row)
            continue

        existing = _find_supplier(db, row)
        if existing:
            opening = parse_decimal(row.get("Opening Balance"))
            if opening is not None:
                existing.opening_balance = opening
                existing.outstanding_balance = opening
            else:
                result.add_skipped(row_num, "Supplier already exists", row)
                continue
            result.imported += 1
            continue

        supplier_id = normalize_text(row.get("Supplier ID"))
        if not supplier_id:
            supplier_id = f"SUP-{1000 + result.imported + 1:04d}"

        supplier = Supplier(
            supplier_id=supplier_id,
            name=name or contact or "Unknown",
            company_name=normalize_text(row.get("Company Name") or row.get("Company")),
            gst_no=normalize_text(row.get("GST No") or row.get("GSTIN")),
            pan_no=normalize_text(row.get("PAN No") or row.get("PAN")),
            contact_number=contact or "",
            contact_number_2=normalize_text(row.get("Contact Number 2") or row.get("Contact 2")),
            email=normalize_text(row.get("Email")),
            address=normalize_text(row.get("Address")),
            account_no=normalize_text(row.get("Account No") or row.get("Bank Account")),
            ifsc_code=normalize_text(row.get("IFSC Code") or row.get("IFSC")),
            bank_detail=normalize_text(row.get("Bank Detail") or row.get("Bank Name")),
            cash=parse_decimal(row.get("Cash")) or Decimal("0"),
            notes=normalize_text(row.get("Notes")),
            opening_balance=parse_decimal(row.get("Opening Balance")) or Decimal("0"),
            outstanding_balance=parse_decimal(row.get("Outstanding Balance")) or Decimal("0"),
            is_active=True,
        )
        db.add(supplier)
        result.imported += 1

    db.commit()
    return result


def import_raw_materials(
    db: Session, rows: list[dict[str, Any]], import_type: str = "raw_materials"
) -> ImportResult:
    result = ImportResult(import_type)
    result.total_rows = len(rows)

    for idx, row in enumerate(rows):
        row_num = idx + 2
        name = normalize_text(row.get("Raw Material Name") or row.get("Name"))
        if not name:
            result.add_skipped(row_num, "Missing raw material name", row)
            continue

        existing = _find_raw_material(db, row)
        if existing:
            result.add_skipped(row_num, "Raw material already exists", row)
            continue

        material_id = normalize_text(row.get("Material ID") or row.get("Raw Material ID"))
        if not material_id:
            material_id = f"RM-{1000 + result.imported + 1:04d}"

        category = _get_or_create_raw_material_category(db, normalize_text(row.get("Category")))

        unit_text = normalize_lower(row.get("Unit"))
        unit_map = {
            "kg": "kg",
            "kilogram": "kg",
            "kilograms": "kg",
            "g": "gram",
            "gm": "gram",
            "gram": "gram",
            "grams": "gram",
            "pkt": "packet",
            "packet": "packet",
            "packets": "packet",
            "pack": "packet",
            "pc": "pieces",
            "pcs": "pieces",
            "piece": "pieces",
            "pieces": "pieces",
            "nos": "pieces",
            "l": "litres",
            "ltr": "litres",
            "liter": "litres",
            "liters": "litres",
            "litre": "litres",
            "litres": "litres",
            "m": "metres",
            "mtr": "metres",
            "meter": "metres",
            "meters": "metres",
            "metre": "metres",
            "metres": "metres",
        }
        unit = unit_map.get(unit_text, "kg") if unit_text else "kg"

        opening = parse_decimal(row.get("Opening Stock"))
        if opening is None:
            opening = Decimal("0")

        material = RawMaterial(
            material_id=material_id,
            name=name,
            category_id=category.id if category else None,
            unit=unit,
            opening_stock=opening,
            current_stock=opening,
            threshold_stock=parse_decimal(row.get("Threshold Stock") or row.get("Minimum Stock"))
            or Decimal("0"),
            reorder_level=parse_decimal(row.get("Reorder Level")) or Decimal("0"),
            purchase_rate=parse_decimal(row.get("Purchase Rate") or row.get("Rate"))
            or Decimal("0"),
            hsn_code=normalize_hsn(row.get("HSN Code") or row.get("HSN")),
            is_active=True,
        )
        db.add(material)
        result.imported += 1

    db.commit()
    return result


def import_products(
    db: Session, rows: list[dict[str, Any]], import_type: str = "products"
) -> ImportResult:
    """Import products. If a Packing Size / Packaging column is present,
    create packaging sizes too."""
    result = ImportResult(import_type)
    result.total_rows = len(rows)

    for idx, row in enumerate(rows):
        row_num = idx + 2
        name = normalize_text(row.get("Product Name") or row.get("Name"))
        if not name:
            result.add_skipped(row_num, "Missing product name", row)
            continue

        product_id = normalize_text(row.get("Product ID"))
        try:
            with db.begin_nested():
                existing = None
                if product_id:
                    existing = db.query(Product).filter(Product.product_id == product_id).first()
                if not existing:
                    existing = db.query(Product).filter(Product.name == name).first()
                if not existing:
                    existing = _match_product_by_name(db, name)
                if not existing and product_id is None:
                    # Generated ids must skip over ids already in the DB
                    # (earlier imports, hand-keyed series): blindly using the
                    # counter collides and the savepoint below would wrongly
                    # skip a brand-new product.
                    n = 1000 + result.imported + 1
                    product_id = f"PROD-{n:04d}"
                    while db.query(Product).filter(
                        Product.product_id == product_id
                    ).first():
                        n += 1
                        product_id = f"PROD-{n:04d}"

                if not existing:
                    category = _get_or_create_product_category(
                        db, normalize_text(row.get("Category"))
                    )
                    product = Product(
                        product_id=product_id,
                        name=name,
                        category_id=category.id if category else None,
                        description=normalize_text(row.get("Description")),
                        base_rate_per_kg=parse_decimal(
                            row.get("Rate per Kg") or row.get("Price per Kg")
                        )
                        or Decimal("0"),
                        hsn_code=normalize_hsn(row.get("HSN Code") or row.get("HSN")),
                        is_active=True,
                    )
                    db.add(product)
                    db.flush()
                else:
                    product = existing
                    # A re-imported row tops up a blank HSN but never overwrites one
                    # already set (same no-clobber rule as the rate fill below).
                    if not product.hsn_code:
                        product.hsn_code = normalize_hsn(row.get("HSN Code") or row.get("HSN"))

                # Packaging size
                size_name = normalize_text(
                    row.get("Packaging Size") or row.get("Pack Size") or row.get("Size")
                )
                if not size_name:
                    size_name = "Loose Powder"
                # Same rule as _attach_sizes_to_product: bare "loose"
                # spellings merge into the bulk size instead of sprouting.
                if size_name.casefold() in ("loose", "loose masala"):
                    size_name = "Loose Powder"

                existing_pack = None
                for ps in product.packaging_sizes:
                    if (ps.size_name or "").strip().casefold() == size_name.casefold():
                        existing_pack = ps
                        break

                weight = parse_decimal(row.get("Weight (kg)") or row.get("Weight"))
                if weight is None:
                    size_match = re.search(r"(\d+)\s*(g|gm|kg)", size_name.lower())
                    if size_match:
                        num = float(size_match.group(1))
                        unit_s = size_match.group(2)
                        weight = Decimal(str(num / 1000 if unit_s in ("g", "gm") else num))
                    else:
                        weight = Decimal("1")

                rate_per_kg = parse_decimal(row.get("Rate per Kg") or row.get("Price"))
                if rate_per_kg is None:
                    rate_per_kg = product.base_rate_per_kg

                if existing_pack:
                    existing_pack.weight_in_kg = weight
                    existing_pack.rate_per_kg = rate_per_kg
                    # A re-imported size comes back into use, mirroring the
                    # restore-on-re-add rule in _attach_sizes_to_product: without
                    # this the import reports success while the size stays hidden.
                    existing_pack.is_active = True
                else:
                    pack = PackagingSize(
                        product_id=product.id,
                        size_name=size_name,
                        weight_in_kg=weight,
                        rate_per_kg=rate_per_kg,
                        packets_per_unit=1,
                        is_active=True,
                    )
                    db.add(pack)

                # Flush the whole row here so a duplicate surfaces inside this
                # row's savepoint (and is skipped as this row), not at the
                # final commit or the next row's first query. Expire the size
                # collection too: it was loaded before this row's pack was
                # added, so without this a repeated size later in the same
                # file never sees it and duplicates it.
                db.flush()
                db.expire(product, ["packaging_sizes"])
                result.imported += 1
        except IntegrityError:
            # A duplicate that slipped past the pre-checks (same id twice in
            # one file, or the same file submitted twice concurrently): skip
            # the row instead of killing the whole batch.
            result.add_skipped(
                row_num, f"Product {product_id or name} already exists", row
            )
            continue

    db.commit()
    return result


def import_purchase_invoices(
    db: Session, rows: list[dict[str, Any]], import_type: str = "purchase_invoices"
) -> ImportResult:
    """Import purchase invoices. Rows can contain repeated invoice numbers
    for multiple line items."""
    result = ImportResult(import_type)
    result.total_rows = len(rows)

    # Group rows by invoice number
    invoice_groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for row in rows:
        invoice_number = normalize_text(
            row.get("Invoice Number") or row.get("Invoice No") or row.get("Invo No")
        )
        if not invoice_number:
            invoice_number = f"PUR-{len(rows):04d}-{len(invoice_groups) + 1:04d}"
        if invoice_number not in invoice_groups:
            invoice_groups[invoice_number] = []
            order.append(invoice_number)
        invoice_groups[invoice_number].append(row)

    for invoice_number in order:
        group = invoice_groups[invoice_number]
        first = group[0]

        existing = (
            db.query(PurchaseInvoice)
            .filter(PurchaseInvoice.invoice_number == invoice_number)
            .first()
        )
        if existing:
            result.add_skipped(len(group) - 1, f"Invoice {invoice_number} already exists", first)
            continue

        invoice_date = parse_date(first.get("Invoice Date") or first.get("Date")) or _ist_wall()
        supplier = _find_supplier(db, first)
        if not supplier:
            result.add_skipped(1, "Supplier not found", first)
            continue

        items = []
        total = Decimal("0")
        for item_row in group:
            material = _find_raw_material(db, item_row)
            if not material:
                result.add_skipped(
                    1, f"Raw material not found for invoice {invoice_number}", item_row
                )
                continue
            quantity = parse_decimal(item_row.get("Quantity")) or Decimal("0")
            rate = parse_decimal(item_row.get("Rate")) or Decimal("0")
            gst_percent = parse_decimal(item_row.get("GST %") or item_row.get("GST")) or Decimal(
                "0"
            )
            amount = quantity * rate
            gst_amount = amount * gst_percent / Decimal("100")
            item_total = amount + gst_amount

            items.append(
                {
                    "raw_material": material,
                    "quantity": quantity,
                    "unit": normalize_lower(item_row.get("Unit")) or "kg",
                    "rate": rate,
                    "gst_percent": gst_percent,
                    "gst_amount": gst_amount,
                    "amount": item_total,
                }
            )
            total += item_total

        if not items:
            result.add_skipped(1, f"No valid items for invoice {invoice_number}", first)
            continue

        total_amount = total
        paid = parse_decimal(first.get("Paid Amount") or first.get("Paid")) or Decimal("0")
        outstanding = total_amount - paid
        if outstanding < 0:
            outstanding = Decimal("0")

        invoice = PurchaseInvoice(
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            supplier_id=supplier.id,
            gst_percent=total if False else Decimal("0"),
            cgst=Decimal("0"),
            sgst=Decimal("0"),
            igst=Decimal("0"),
            other_charges=parse_decimal(first.get("Other Charges")) or Decimal("0"),
            discount=parse_decimal(first.get("Discount")) or Decimal("0"),
            total_amount=total_amount,
            payment_status="paid" if outstanding == 0 else "pending",
            paid_amount=paid,
            outstanding_amount=outstanding,
            notes=normalize_text(first.get("Notes")),
            is_confirmed=True,
        )
        db.add(invoice)
        db.flush()

        for item in items:
            db_item = PurchaseInvoiceItem(
                purchase_invoice_id=invoice.id,
                raw_material_id=item["raw_material"].id,
                quantity=item["quantity"],
                unit=item["unit"],
                rate=item["rate"],
                gst_percent=item["gst_percent"],
                cgst_amount=item["gst_amount"] / 2
                if item["gst_percent"]
                else item["gst_amount"] / 2,
                sgst_amount=item["gst_amount"] / 2 if item["gst_percent"] else Decimal("0"),
                igst_amount=Decimal("0"),
                amount=item["amount"],
            )
            db.add(db_item)
            # Update raw material stock
            if invoice.is_confirmed:
                item["raw_material"].current_stock = (
                    item["raw_material"].current_stock or Decimal("0")
                ) + item["quantity"]

        result.imported += 1

    db.commit()
    return result


def import_sales_challans(
    db: Session, rows: list[dict[str, Any]], import_type: str = "sales_challans"
) -> ImportResult:
    """Import sales challans. Rows can contain repeated challan numbers for multiple line items."""
    result = ImportResult(import_type)
    result.total_rows = len(rows)

    challan_groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for row in rows:
        challan_number = normalize_text(
            row.get("Challan Number")
            or row.get("Challan No")
            or row.get("Bill No")
            or row.get("Invoice No")
        )
        if not challan_number:
            challan_number = f"CH-{len(rows):05d}-{len(challan_groups) + 1:05d}"
        if challan_number not in challan_groups:
            challan_groups[challan_number] = []
            order.append(challan_number)
        challan_groups[challan_number].append(row)

    for challan_number in order:
        group = challan_groups[challan_number]
        first = group[0]

        existing = (
            db.query(SalesChallan)
            .filter(func.lower(SalesChallan.challan_number) == challan_number.lower())
            .first()
        )
        if existing:
            # Self-heal: challans imported by the old code were born confirmed
            # but never posted stock. Re-importing the same file must backfill
            # the missing movements instead of skipping silently. The challan
            # itself (and its dues) stay untouched - they were correct already.
            if post_challan_sales_movements(db, existing, existing.items or []):
                result.add_skipped(
                    len(group),
                    f"Challan {challan_number} already exists - backfilled missing stock movements",
                    first,
                )
            else:
                result.add_skipped(
                    len(group) - 1, f"Challan {challan_number} already exists", first
                )
            continue

        challan_date = (
            parse_date(first.get("Challan Date") or first.get("Date") or first.get("Bill Date"))
            or _ist_wall()
        )
        customer = _find_customer(db, first)
        if not customer:
            result.add_skipped(1, "Customer not found", first)
            continue

        items = []
        total_price = Decimal("0")
        total_weight = Decimal("0")

        for item_row in group:
            product = _find_product(db, item_row)
            if not product:
                product_name = normalize_text(
                    item_row.get("Product Name") or item_row.get("Product")
                )
                if not product_name:
                    result.add_skipped(1, "Product not found", item_row)
                    continue
                # Generated ids must be unique per product AND skip over ids
                # already in the DB: len(rows) is the whole file's row count,
                # so reusing it verbatim stamps every new product with the
                # same id and the second one dies on UNIQUE (PROD-11040).
                new_prod_n = 9000 + len(rows)
                new_prod_id = f"PROD-{new_prod_n:04d}"
                while db.query(Product).filter(
                    Product.product_id == new_prod_id
                ).first():
                    new_prod_n += 1
                    new_prod_id = f"PROD-{new_prod_n:04d}"
                product = Product(
                    product_id=new_prod_id,
                    name=product_name,
                    is_active=True,
                )
                db.add(product)
                db.flush()

            size_name = normalize_text(
                item_row.get("Packaging Size") or item_row.get("Pack Size") or item_row.get("Size")
            )
            if not size_name:
                size_name = "Loose Powder"

            packaging_size = None
            for ps in product.packaging_sizes:
                if ps.size_name == size_name:
                    packaging_size = ps
                    break
            if not packaging_size:
                packaging_size = PackagingSize(
                    product_id=product.id,
                    size_name=size_name,
                    weight_in_kg=Decimal("1"),
                    rate_per_kg=product.base_rate_per_kg or Decimal("0"),
                    packets_per_unit=1,
                    is_active=True,
                )
                db.add(packaging_size)
                db.flush()

            quantity = parse_decimal(item_row.get("Quantity") or item_row.get("Qty")) or Decimal(
                "0"
            )
            # The pack master is authoritative: a file Weight column has been
            # seen echoing packet counts as kg (120 pkts of 250g stored as
            # 120 kg), so derive weight from qty x pack weight whenever the
            # size resolved instead of trusting the file.
            weight = quantity * packaging_size.weight_in_kg
            rate_per_kg = parse_decimal(item_row.get("Rate per Kg") or item_row.get("Rate"))
            if rate_per_kg is None:
                rate_per_kg = packaging_size.rate_per_kg or Decimal("0")
            amount = parse_decimal(item_row.get("Amount") or item_row.get("Total"))
            if amount is None:
                amount = weight * rate_per_kg

            items.append(
                {
                    "product": product,
                    "packaging_size": packaging_size,
                    "quantity": quantity,
                    "weight": weight,
                    "rate_per_kg": rate_per_kg,
                    "amount": amount,
                }
            )
            total_price += amount
            total_weight += weight

        if not items:
            result.add_skipped(1, f"No valid items for challan {challan_number}", first)
            continue

        paid = parse_decimal(
            first.get("Paid Amount") or first.get("Paid") or first.get("Received")
        ) or Decimal("0")
        total_received = paid
        outstanding = total_price - total_received
        if outstanding < 0:
            outstanding = Decimal("0")

        status = "paid" if outstanding == 0 else ("partially_paid" if paid > 0 else "confirmed")

        # Same freeze as the challan route, including the default print
        # footer: an imported challan is an issued document too, so it must
        # not read the company profile at print time.
        footer_id, footer_terms = default_footer(db)
        profile = company_profile(db)
        if footer_terms:
            profile["terms"] = footer_terms
        challan = SalesChallan(
            challan_number=challan_number,
            challan_date=challan_date,
            customer_id=customer.id,
            status=status,
            total_weight=total_weight,
            total_price=total_price,
            paid_amount=total_received,
            outstanding_amount=outstanding,
            notes=normalize_text(first.get("Notes")),
            footer_id=footer_id,
        )
        apply_snapshot(challan, profile, customer)
        db.add(challan)
        db.flush()

        db_items = []
        for item in items:
            db_item = SalesChallanItem(
                challan_id=challan.id,
                product_id=item["product"].id,
                packaging_size_id=item["packaging_size"].id,
                quantity=item["quantity"],
                weight=item["weight"],
                rate_per_kg=item["rate_per_kg"],
                amount=item["amount"],
                # Freeze the printed description while product/size are resolved.
                product_name=item["product"].name,
                size_name=item["packaging_size"].size_name,
            )
            db.add(db_item)
            db_items.append(db_item)

        # An imported challan is born issued (never a draft), so it reduces
        # finished-goods stock the same way confirming a manual challan does.
        post_challan_sales_movements(db, challan, db_items)

        # Customer outstanding
        customer.outstanding_balance = (customer.outstanding_balance or Decimal("0")) + outstanding

        # If there was any payment, record it
        payment_mode_raw = first.get("Payment Mode")
        if total_received > 0:
            payment = CustomerPayment(
                challan_id=challan.id,
                payment_date=challan_date,
                amount=total_received,
                payment_mode=parse_payment_mode(payment_mode_raw),
                transaction_ref=normalize_text(
                    first.get("Transaction Ref") or first.get("Reference No")
                ),
                notes=normalize_text(first.get("Payment Notes")),
            )
            db.add(payment)

        result.imported += 1

    db.commit()
    return result


def import_customer_payments(
    db: Session, rows: list[dict[str, Any]], import_type: str = "customer_payments"
) -> ImportResult:
    """Import payments received against EXISTING challans (e.g. old challans
    entered earlier without their receipts). One row = one payment; several
    rows may name the same challan for part-payments.

    Validation mirrors the Payment button exactly (unknown challan, draft,
    archived, non-positive or over-outstanding amount), so a row the UI
    would refuse is skipped with the reason instead of half-posting.
    """

    result = ImportResult(import_type)
    result.total_rows = len(rows)

    for n, row in enumerate(rows, 1):
        number = normalize_text(
            row.get("Challan Number") or row.get("Challan No") or row.get("Bill No")
        )
        if not number:
            result.add_skipped(n, "Challan Number is required", row)
            continue
        ch = (
            db.query(SalesChallan)
            .filter(func.lower(SalesChallan.challan_number) == number.lower())
            .first()
        )
        if not ch:
            result.add_skipped(
                n, f"Challan {number} not found - import sales challans first", row
            )
            continue
        if str(getattr(ch.status, "value", ch.status) or "") == "draft":
            result.add_skipped(
                n, f"Challan {number} is still a draft - confirm it before recording payments", row
            )
            continue
        if ch.is_archived:
            result.add_skipped(
                n, f"Challan {number} is archived - restore it before recording payments", row
            )
            continue
        amount = parse_decimal(
            row.get("Amount") or row.get("Received") or row.get("Paid")
        ) or Decimal("0")
        if amount <= 0:
            result.add_skipped(n, "Amount must be greater than 0", row)
            continue
        outstanding = Decimal(str(ch.total_price or 0)) - Decimal(str(ch.paid_amount or 0))
        if amount > outstanding:
            result.add_skipped(
                n, f"Amount exceeds outstanding (₹ {outstanding}) for challan {number}", row
            )
            continue
        pay_date = (
            parse_date(row.get("Payment Date") or row.get("Date")) or _ist_wall()
        )
        db.add(
            CustomerPayment(
                challan_id=ch.id,
                payment_date=pay_date,
                amount=amount,
                payment_mode=parse_payment_mode(row.get("Payment Mode")),
                transaction_ref=normalize_text(
                    row.get("Transaction Ref") or row.get("Reference No")
                ),
                notes=normalize_text(row.get("Payment Notes") or row.get("Notes")),
            )
        )
        ch.paid_amount = Decimal(str(ch.paid_amount or 0)) + amount
        ch.outstanding_amount = Decimal(str(ch.total_price or 0)) - ch.paid_amount
        if ch.outstanding_amount <= 0:
            ch.outstanding_amount = Decimal("0")
            ch.status = "paid"
        else:
            ch.status = "partially_paid"
        if ch.customer and ch.customer.outstanding_balance:
            ch.customer.outstanding_balance = max(
                Decimal("0"),
                Decimal(str(ch.customer.outstanding_balance)) - amount,
            )
        result.imported += 1

    db.commit()
    return result


def import_expenses(
    db: Session, rows: list[dict[str, Any]], import_type: str = "expenses"
) -> ImportResult:
    result = ImportResult(import_type)
    result.total_rows = len(rows)

    for idx, row in enumerate(rows):
        row_num = idx + 2
        amount = parse_decimal(row.get("Amount") or row.get("Expense Amount"))
        description = normalize_text(row.get("Description") or row.get("Expense Description"))
        if amount is None and not description:
            result.add_skipped(row_num, "Missing amount and description", row)
            continue
        if amount is None:
            amount = Decimal("0")

        category = _get_or_create_category(
            db,
            normalize_text(row.get("Category") or row.get("Expense Category")) or "Miscellaneous",
        )

        expense = Expense(
            category_id=category.id if category else None,
            description=description or "Imported expense",
            amount=amount,
            date=parse_date(row.get("Date") or row.get("Expense Date")) or _ist_wall(),
            payment_mode=parse_payment_mode(row.get("Payment Mode")),
            payee=normalize_text(row.get("Payee") or row.get("Paid To")),
            notes=normalize_text(row.get("Notes")),
        )
        db.add(expense)
        result.imported += 1

    db.commit()
    return result


def import_employees(
    db: Session, rows: list[dict[str, Any]], import_type: str = "employees"
) -> ImportResult:
    result = ImportResult(import_type)
    result.total_rows = len(rows)

    for idx, row in enumerate(rows):
        row_num = idx + 2
        name = normalize_text(row.get("Employee Name") or row.get("Name"))
        if not name:
            result.add_skipped(row_num, "Missing employee name", row)
            continue

        employee_id = normalize_text(row.get("Employee ID"))
        existing_manager = (
            db.query(Employee).filter(Employee.employee_id == employee_id).first()
            if employee_id
            else None
        )
        existing = existing_manager or db.query(Employee).filter(Employee.name == name).first()
        if existing:
            result.add_skipped(row_num, "Employee already exists", row)
            continue

        if not employee_id:
            employee_id = f"EMP-{1000 + result.imported + 1:04d}"

        emp_type_raw = normalize_lower(row.get("Employee Type"))
        emp_type = "full_time"
        if emp_type_raw:
            if "part" in emp_type_raw:
                emp_type = "part_time"
            elif "contract" in emp_type_raw:
                emp_type = "contractor"

        status_raw = normalize_lower(row.get("Status"))
        emp_status = "active"
        if status_raw:
            if "inactive" in status_raw or "left" in status_raw:
                emp_status = "inactive"
            elif "leave" in status_raw:
                emp_status = "on_leave"

        employee = Employee(
            employee_id=employee_id,
            name=name,
            email=normalize_text(row.get("Email")),
            phone=normalize_text(row.get("Phone") or row.get("Contact")),
            address=normalize_text(row.get("Address")),
            employee_type=emp_type,
            status=emp_status,
            joining_date=parse_date(row.get("Joining Date") or row.get("Join Date")),
            salary=parse_decimal(row.get("Salary")) or Decimal("0"),
            bank_account_no=normalize_text(row.get("Bank Account No") or row.get("Account No")),
            ifsc_code=normalize_text(row.get("IFSC Code") or row.get("IFSC")),
            pan_no=normalize_text(row.get("PAN No") or row.get("PAN")),
            aadhaar_no=normalize_text(row.get("Aadhaar No") or row.get("Aadhaar")),
            emergency_contact=normalize_text(row.get("Emergency Contact")),
            emergency_name=normalize_text(row.get("Emergency Name")),
            notes=normalize_text(row.get("Notes")),
        )
        db.add(employee)
        result.imported += 1

    db.commit()
    return result


# ---------------------------------------------------------------------------
# Generic import dispatcher
# ---------------------------------------------------------------------------

IMPORTERS = {
    "customers": import_customers,
    "suppliers": import_suppliers,
    "raw_materials": import_raw_materials,
    "products": import_products,
    "purchase_invoices": import_purchase_invoices,
    "sales_challans": import_sales_challans,
    "customer_payments": import_customer_payments,
    "expenses": import_expenses,
    "employees": import_employees,
}


def read_excel_file(
    file_content: bytes, sheet_name: str | None = None
) -> tuple[list[str], list[dict[str, Any]]]:
    """Read an Excel file and return (headers, data_rows).

    The first row is treated as headers. Each subsequent row is converted to a dict.
    All cells are preserved as their native Excel value.
    """

    wb = load_workbook(BytesIO(file_content), data_only=True)
    ws = wb[sheet_name] if sheet_name else wb.worksheets[0]

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return [], []

    headers = []
    for cell in rows[0]:
        header = str(cell).strip() if cell is not None else ""
        headers.append(header)
    headers = [h or f"Column {i + 1}" for i, h in enumerate(headers)]

    data_rows = []
    for row in rows[1:]:
        if all(cell is None or str(cell).strip() == "" for cell in row):
            continue
        data_rows.append(
            {headers[i]: row[i] if i < len(row) else None for i in range(len(headers))}
        )

    return headers, data_rows


def _missing_masters(db: Session, import_type: str) -> list[str]:
    """Master datasets a dependent import needs but the database lacks.

    Challan/invoice rows resolve their masters per row and are skipped when
    absent, so importing them into an empty database skips everything while
    looking like it ran. Stopping up front with a named warning beats a
    hundred identical "not found" skips. Masters themselves need no guard:
    their only skips are bad rows or duplicates.
    """
    if import_type == "purchase_invoices":
        missing = []
        if db.query(Supplier).count() == 0:
            missing.append("suppliers")
        if db.query(RawMaterial).count() == 0:
            missing.append("raw materials")
        return missing
    if import_type == "sales_challans":
        # Products (and their packaging sizes) are auto-created on import;
        # only the customer must already exist.
        if db.query(Customer).count() == 0:
            return ["customers"]
        return []
    if import_type == "customer_payments":
        # Payments land on challans that must already exist.
        missing = []
        if db.query(Customer).count() == 0:
            missing.append("customers")
        if db.query(SalesChallan).count() == 0:
            missing.append("sales challans")
        return missing
    return []


def run_import(
    db: Session, import_type: str, file_content: bytes, sheet_name: str | None = None
) -> dict[str, Any]:
    """Dispatch an import to the correct importer and return the result summary."""
    if import_type not in IMPORTERS:
        raise ValueError(
            f"Unknown import type: {import_type}. Supported: {', '.join(IMPORTERS.keys())}"
        )

    _, data_rows = read_excel_file(file_content, sheet_name)
    missing = _missing_masters(db, import_type)
    if missing:
        total = len(data_rows)
        return {
            "import_type": import_type,
            "total_rows": total,
            "imported": 0,
            "skipped": total,
            "failed": 0,
            "success": False,
            "all_skipped": total > 0,
            "warning": (
                f"No {' and '.join(missing)} found. Import "
                f"{' and '.join(missing)} first (recommended order: customers, "
                "suppliers, raw materials, products, then invoices and challans); "
                "nothing was imported."
            ),
            "errors": [],
            "skipped_rows": [],
        }
    importer = IMPORTERS[import_type]
    result = importer(db, data_rows, import_type)
    return result.summary()
