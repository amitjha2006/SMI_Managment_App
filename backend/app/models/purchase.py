import enum

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import relationship

from backend.app.core.database import Base, _ist_wall


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    PARTIAL = "partial"
    PAID = "paid"
    CANCELLED = "cancelled"


class PurchaseInvoice(Base):
    __tablename__ = "purchase_invoices"

    id = Column(Integer, primary_key=True, index=True)
    invoice_number = Column(String(50), unique=True, index=True, nullable=False)
    invoice_date = Column(DateTime(timezone=True), nullable=False, index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    gst_percent = Column(Numeric(5, 2), default=0)
    cgst = Column(Numeric(15, 2), default=0)
    sgst = Column(Numeric(15, 2), default=0)
    igst = Column(Numeric(15, 2), default=0)
    other_charges = Column(Numeric(15, 2), default=0)
    # Merchant-side costing only (freight etc.): the payable total and supplier
    # dues exclude this; it feeds the per-kg landed cost on each line.
    transport_charges = Column(Numeric(15, 2), default=0)
    discount = Column(Numeric(15, 2), default=0)
    total_amount = Column(Numeric(15, 2), default=0)
    payment_status = Column(SQLEnum(PaymentStatus), default=PaymentStatus.PENDING)
    paid_amount = Column(Numeric(15, 2), default=0)
    outstanding_amount = Column(Numeric(15, 2), default=0)
    attachment_path = Column(String(500), nullable=True)
    notes = Column(Text, nullable=True)
    is_confirmed = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)
    updated_at = Column(DateTime(timezone=True), onupdate=_ist_wall)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    is_archived = Column(Boolean, default=False, nullable=False)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    archived_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    supplier = relationship("Supplier", back_populates="purchase_invoices")
    items = relationship(
        "PurchaseInvoiceItem", back_populates="purchase_invoice", cascade="all, delete-orphan"
    )
    payments = relationship(
        "SupplierPayment", back_populates="purchase_invoice", cascade="all, delete-orphan"
    )


class PurchaseInvoiceItem(Base):
    __tablename__ = "purchase_invoice_items"

    id = Column(Integer, primary_key=True, index=True)
    purchase_invoice_id = Column(Integer, ForeignKey("purchase_invoices.id"), nullable=False)
    raw_material_id = Column(Integer, ForeignKey("raw_materials.id"), nullable=False)
    quantity = Column(Numeric(15, 3), nullable=False)
    unit = Column(String(20), nullable=False)
    rate = Column(Numeric(15, 2), nullable=False)
    gst_percent = Column(Numeric(5, 2), default=0)
    cgst_amount = Column(Numeric(15, 2), default=0)
    sgst_amount = Column(Numeric(15, 2), default=0)
    igst_amount = Column(Numeric(15, 2), default=0)
    amount = Column(Numeric(15, 2), default=0)
    # This line's share of the invoice's transport and other charges,
    # allocated pro-rata to kg (see create_purchase_invoice).
    transport_amount = Column(Numeric(15, 2), default=0)
    other_amount = Column(Numeric(15, 2), default=0)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)

    purchase_invoice = relationship("PurchaseInvoice", back_populates="items")
    raw_material = relationship("RawMaterial", back_populates="purchase_items")


class SupplierPayment(Base):
    __tablename__ = "supplier_payments"

    id = Column(Integer, primary_key=True, index=True)
    purchase_invoice_id = Column(Integer, ForeignKey("purchase_invoices.id"), nullable=False)
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

    purchase_invoice = relationship("PurchaseInvoice", back_populates="payments")
