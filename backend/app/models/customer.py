from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from backend.app.core.database import Base, _ist_wall


class CashMovement(Base):
    """A single cash event against a customer or supplier.

    The `cash` column on customers and suppliers existed from the start but
    nothing ever read or wrote it, so it sat in the schema looking meaningful
    while carrying no information. The business confirmed its intent: it is
    the cash float they hold with you.

    Rather than overwrite a running total - which loses the history and makes
    a mistake unrecoverable - each event is stored as a row and the balance is
    derived by summing. `customer_cash` / `supplier_cash` on the master rows
    are kept as the opening float only, so the derived balance is
    opening + sum( movements ).

    One table for both parties because the rules are identical; exactly one of
    the two foreign keys is set, enforced by the CHECK constraints below so a
    row cannot belong to nobody or to both.
    """

    __tablename__ = "cash_movements"
    __table_args__ = (
        CheckConstraint(
            "(customer_id IS NOT NULL AND supplier_id IS NULL) OR "
            "(supplier_id IS NOT NULL AND customer_id IS NULL)",
            name="ck_cash_movement_one_party",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True)
    movement_date = Column(DateTime(timezone=True), nullable=False)
    # 'received' = they gave us cash (balance rises). 'paid' = we gave them
    # cash back or against a bill (balance falls). Named from the factory's
    # point of view, which is the only view that stays unambiguous for both
    # customers and suppliers.
    movement_type = Column(String(20), nullable=False)  # received, paid
    amount = Column(Numeric(15, 2), nullable=False)
    reference = Column(String(120), nullable=True)  # free text: "chit 421", "against CH-45181"
    notes = Column(Text, nullable=True)
    # A movement can be undone without deleting history: flip is_void, and the
    # balance calculation ignores voided rows. Deleting a cash row is how
    # balances quietly stop reconciling.
    is_void = Column(Boolean, default=False, nullable=False)
    voided_at = Column(DateTime(timezone=True), nullable=True)
    voided_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    customer = relationship("Customer", back_populates="cash_movements")
    supplier = relationship("Supplier", back_populates="cash_movements")


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(String(100), nullable=False)
    company_name = Column(String(100), nullable=True)
    gst_no = Column(String(15), nullable=True)
    pan_no = Column(String(10), nullable=True)
    contact_number = Column(String(15), nullable=False)
    contact_number_2 = Column(String(15), nullable=True)
    email = Column(String(100), nullable=True)
    address = Column(Text, nullable=True)
    account_no = Column(String(50), nullable=True)
    ifsc_code = Column(String(11), nullable=True)
    bank_detail = Column(Text, nullable=True)
    # Opening cash float, NOT the running balance. The current balance is
    # derived as this + the sum of cash_movements, so the history survives and
    # a mistyped amount can be voided rather than silently overwritten.
    # See CashMovement.
    cash = Column(Numeric(15, 2), default=0)
    notes = Column(Text, nullable=True)
    opening_balance = Column(Numeric(15, 2), default=0)
    outstanding_balance = Column(Numeric(15, 2), default=0)
    # Credit limit in Rs, enforced at challan confirm time. 0 (default) means
    # unlimited. An Editor Mode session may override it; the override is
    # written to the audit trail with the shortfall.
    credit_limit = Column(Numeric(15, 2), default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)
    updated_at = Column(DateTime(timezone=True), onupdate=_ist_wall)

    challans = relationship("SalesChallan", back_populates="customer")
    cash_movements = relationship(
        "CashMovement", back_populates="customer", cascade="all, delete-orphan"
    )


class Supplier(Base):
    __tablename__ = "suppliers"

    id = Column(Integer, primary_key=True, index=True)
    supplier_id = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(String(100), nullable=False)
    company_name = Column(String(100), nullable=True)
    gst_no = Column(String(15), nullable=True)
    pan_no = Column(String(10), nullable=True)
    contact_number = Column(String(15), nullable=False)
    contact_number_2 = Column(String(15), nullable=True)
    email = Column(String(100), nullable=True)
    address = Column(Text, nullable=True)
    account_no = Column(String(50), nullable=True)
    ifsc_code = Column(String(11), nullable=True)
    bank_detail = Column(Text, nullable=True)
    # Opening cash float, not the running balance - see CashMovement.
    cash = Column(Numeric(15, 2), default=0)
    notes = Column(Text, nullable=True)
    opening_balance = Column(Numeric(15, 2), default=0)
    outstanding_balance = Column(Numeric(15, 2), default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)
    updated_at = Column(DateTime(timezone=True), onupdate=_ist_wall)

    purchase_invoices = relationship("PurchaseInvoice", back_populates="supplier")
    cash_movements = relationship(
        "CashMovement", back_populates="supplier", cascade="all, delete-orphan"
    )
