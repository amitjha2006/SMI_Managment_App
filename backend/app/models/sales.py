import enum

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import relationship

from backend.app.core.database import Base, _ist_wall


class ChallanStatus(str, enum.Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    CLOSED = "closed"
    DISPATCHED = "dispatched"
    DELIVERED = "delivered"


class SalesChallan(Base):
    __tablename__ = "sales_challans"

    id = Column(Integer, primary_key=True, index=True)
    challan_number = Column(String(50), unique=True, index=True, nullable=False)
    challan_date = Column(DateTime(timezone=True), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    status = Column(SQLEnum(ChallanStatus), default=ChallanStatus.DRAFT)
    total_weight = Column(Numeric(15, 3), default=0)  # in kg
    total_price = Column(Numeric(15, 2), default=0)
    paid_amount = Column(Numeric(15, 2), default=0)
    outstanding_amount = Column(Numeric(15, 2), default=0)
    notes = Column(Text, nullable=True)
    attachment_path = Column(String(500), nullable=True)
    dispatched_at = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    transporter = Column(String(100), nullable=True)
    vehicle_no = Column(String(50), nullable=True)
    # Frozen print header. Copied from the company profile and the customer at
    # creation time so editing either master later cannot alter what this
    # already-issued document says. See services/challan_snapshot.py.
    # company_snapshot_at marks "stamped"; company_name alone cannot, because a
    # deliberately blank name is a legitimate value.
    company_name = Column(Text, nullable=True)
    company_address = Column(Text, nullable=True)
    company_gstin = Column(String(15), nullable=True)
    company_phone = Column(String(50), nullable=True)
    company_email = Column(Text, nullable=True)
    company_terms = Column(Text, nullable=True)
    cust_company_name = Column(Text, nullable=True)
    cust_address = Column(Text, nullable=True)
    cust_gst_no = Column(String(15), nullable=True)
    cust_contact_number = Column(String(50), nullable=True)
    company_snapshot_at = Column(DateTime(timezone=True), nullable=True)
    # Which print footer was picked (see print_footers). Nullable plain
    # integer, deliberately not a FK: the frozen company_terms text above is
    # what prints, so a deleted footer can never break an old document.
    footer_id = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)
    updated_at = Column(DateTime(timezone=True), onupdate=_ist_wall)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    # Spec 3/15: archive instead of permanent deletion. Archiving keeps the
    # financial/stock effect in historical totals; it only hides the row.
    is_archived = Column(Boolean, default=False, nullable=False)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    archived_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    customer = relationship("Customer", back_populates="challans")
    items = relationship("SalesChallanItem", back_populates="challan", cascade="all, delete-orphan")
    payments = relationship(
        "CustomerPayment", back_populates="challan", cascade="all, delete-orphan"
    )
    # The tax invoice raised from this challan, if any. uselist=False because
    # sales_invoices.challan_id is UNIQUE - one invoice per challan, enforced
    # by the database rather than by convention.
    invoice = relationship("SalesInvoice", back_populates="challan", uselist=False)


class SalesChallanItem(Base):
    __tablename__ = "sales_challan_items"

    id = Column(Integer, primary_key=True, index=True)
    challan_id = Column(Integer, ForeignKey("sales_challans.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    packaging_size_id = Column(Integer, ForeignKey("packaging_sizes.id"), nullable=False)
    quantity = Column(Numeric(15, 3), nullable=False)  # quantity in packets/units
    weight = Column(Numeric(15, 3), nullable=False)  # weight in kg
    rate_per_kg = Column(Numeric(15, 2), nullable=False)
    amount = Column(Numeric(15, 2), nullable=False)  # total for this line item
    notes = Column(Text, nullable=True)
    # Frozen line description, copied at creation. quantity/weight/rate/amount
    # above are already immutable, so without these a product rename would
    # retroactively change an issued challan.
    product_name = Column(Text, nullable=True)
    size_name = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)

    challan = relationship("SalesChallan", back_populates="items")
    product = relationship("Product", back_populates="sales_items")
    packaging_size = relationship("PackagingSize", back_populates="sales_items")


class SalesInvoice(Base):
    """A sales tax invoice - the final bill sent to a customer.

    This is a DIFFERENT document from the challan, and the difference is the
    whole point:

      delivery challan  - goods have left. Not a tax document. No GST.
      sales invoice      - the bill. Carries GST, and the buyer's accountant
                           needs it to claim input credit.

    An invoice is always created FROM a challan, never entered directly, so
    the two can never disagree about what was sold. The amounts are carried
    across GST-INCLUSIVE and the tax is extracted from them, so the invoice
    total equals the challan total exactly and nobody is re-billed.

    The 89 challans that predate the invoice cut-over cannot be invoiced: they
    were delivered under a different arrangement and are not back-billed.

    Money is NOT tracked here. Payments and outstanding stay on the challan as
    the single source of truth (the business decision), so this table
    deliberately has no paid_amount or outstanding_amount column - duplicating
    them would risk the same receivable being counted twice. Anything about
    payment is read through `challan_id`.
    """

    __tablename__ = "sales_invoices"

    id = Column(Integer, primary_key=True, index=True)
    invoice_number = Column(String(50), unique=True, index=True, nullable=False)
    invoice_date = Column(DateTime(timezone=True), nullable=False, index=True)

    # UNIQUE is the real enforcement of "exactly one invoice per challan". A
    # check in the request handler can be raced or forgotten; this cannot.
    challan_id = Column(Integer, ForeignKey("sales_challans.id"), unique=True, nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)

    # GST decision, frozen at issue time. Whether this was an intra- or
    # inter-state supply is a fact about this document and must never be
    # recomputed later from a company setting that has since changed.
    intra_state = Column(Boolean, default=True, nullable=False)

    total_quantity = Column(Numeric(15, 3), default=0)
    total_weight = Column(Numeric(15, 3), default=0)
    taxable_value = Column(Numeric(15, 2), default=0)
    total_tax = Column(Numeric(15, 2), default=0)
    cgst = Column(Numeric(15, 2), default=0)
    sgst = Column(Numeric(15, 2), default=0)
    igst = Column(Numeric(15, 2), default=0)
    # The GST-inclusive total, which must equal the source challan total.
    grand_total = Column(Numeric(15, 2), default=0)

    # Frozen print header, same discipline as the challan: what a tax document
    # says must not change after it is issued. Unlike the challan these are
    # required for the document to be valid, and invoice creation refuses to
    # issue one if the supplier GSTIN is missing.
    company_name = Column(Text, nullable=True)
    company_address = Column(Text, nullable=True)
    company_gstin = Column(String(15), nullable=True)
    company_phone = Column(String(50), nullable=True)
    company_email = Column(Text, nullable=True)
    company_terms = Column(Text, nullable=True)
    company_snapshot_at = Column(DateTime(timezone=True), nullable=True)

    cust_company_name = Column(Text, nullable=True)
    cust_address = Column(Text, nullable=True)
    cust_gst_no = Column(String(15), nullable=True)
    cust_contact_number = Column(String(50), nullable=True)
    # Where the goods were delivered. On an inter-state invoice this is what
    # justifies IGST, so it is recorded rather than inferred.
    place_of_supply = Column(String(80), nullable=True)

    notes = Column(Text, nullable=True)
    is_archived = Column(Boolean, default=False, nullable=False)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    archived_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    challan = relationship("SalesChallan", back_populates="invoice")
    customer = relationship("Customer")
    items = relationship(
        "SalesInvoiceItem",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="SalesInvoiceItem.sequence",
    )


class SalesInvoiceItem(Base):
    """One product line on a tax invoice.

    `gst_rate` is copied from the product onto the line rather than referenced,
    because a rate change on the product must never alter an invoice that has
    already been issued. Same reasoning as the challan's product_name/size_name
    snapshots.
    """

    __tablename__ = "sales_invoice_items"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("sales_invoices.id"), nullable=False)
    sequence = Column(Integer, default=1, nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    packaging_size_id = Column(Integer, ForeignKey("packaging_sizes.id"), nullable=False)

    product_name = Column(Text, nullable=True)
    size_name = Column(Text, nullable=True)
    hsn_code = Column(String(12), nullable=True)

    quantity = Column(Numeric(15, 3), default=0)
    weight = Column(Numeric(15, 3), default=0)
    rate_per_kg = Column(Numeric(15, 2), default=0)

    # GST-inclusive, straight from the challan line.
    amount = Column(Numeric(15, 2), default=0)
    gst_rate = Column(Numeric(5, 2), default=0, nullable=False)
    taxable_value = Column(Numeric(15, 2), default=0)
    cgst = Column(Numeric(15, 2), default=0)
    sgst = Column(Numeric(15, 2), default=0)
    igst = Column(Numeric(15, 2), default=0)

    invoice = relationship("SalesInvoice", back_populates="items")
    product = relationship("Product")
    packaging_size = relationship("PackagingSize")


class CustomerPayment(Base):
    __tablename__ = "customer_payments"

    id = Column(Integer, primary_key=True, index=True)
    challan_id = Column(Integer, ForeignKey("sales_challans.id"), nullable=False)
    payment_date = Column(DateTime(timezone=True), nullable=False, index=True)
    amount = Column(Numeric(15, 2), nullable=False)
    payment_mode = Column(
        String(30), nullable=False
    )  # cash, upi, bank_transfer, cheque, card, other
    transaction_ref = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    is_archived = Column(Boolean, default=False, nullable=False)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    archived_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    challan = relationship("SalesChallan", back_populates="payments")
