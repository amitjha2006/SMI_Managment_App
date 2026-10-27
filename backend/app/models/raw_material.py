import enum

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import relationship

from backend.app.core.database import Base, _ist_wall


class UnitType(str, enum.Enum):
    KG = "kg"
    GRAM = "gram"
    PACKET = "packet"
    PIECES = "pieces"
    LITRES = "litres"
    METRES = "metres"


class RawMaterialCategory(Base):
    __tablename__ = "raw_material_categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)


class RawMaterial(Base):
    __tablename__ = "raw_materials"

    id = Column(Integer, primary_key=True, index=True)
    material_id = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(String(100), nullable=False)
    category_id = Column(Integer, ForeignKey("raw_material_categories.id"), nullable=True)
    unit = Column(SQLEnum(UnitType), nullable=False)
    opening_stock = Column(Numeric(15, 3), default=0)
    current_stock = Column(Numeric(15, 3), default=0)
    threshold_stock = Column(Numeric(15, 3), default=0)
    reorder_level = Column(Numeric(15, 3), default=0)
    purchase_rate = Column(Numeric(15, 2), default=0)
    # HSN code picked from the curated raw-material list (nullable like
    # products: a wrong code is worse than a missing one).
    hsn_code = Column(String(12), nullable=True)
    is_active = Column(Boolean, default=True)
    # Watch toggle: unwatched materials stay usable everywhere but skip
    # low-stock alerts, snapshot badges, and counts.
    watch_stock = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)
    updated_at = Column(DateTime(timezone=True), onupdate=_ist_wall)

    category = relationship("RawMaterialCategory")

    @property
    def category_name(self):
        """The category NAME, so the API keeps serving a plain string
        even though storage is a reference (same precedent as Product.category)."""
        return self.category.name if self.category else None
    stock_movements = relationship("RawMaterialStockMovement", back_populates="raw_material")
    purchase_items = relationship("PurchaseInvoiceItem", back_populates="raw_material")


class RawMaterialStockMovement(Base):
    __tablename__ = "raw_material_stock_movements"

    id = Column(Integer, primary_key=True, index=True)
    raw_material_id = Column(Integer, ForeignKey("raw_materials.id"), nullable=False)
    movement_type = Column(
        String(20), nullable=False
    )  # purchase, consumption, wastage, adjustment, opening
    reference_type = Column(String(50), nullable=True)  # purchase_invoice, production, etc.
    reference_id = Column(Integer, nullable=True)
    quantity = Column(Numeric(15, 3), nullable=False)
    unit = Column(String(20), nullable=False)
    rate = Column(Numeric(15, 2), default=0)
    notes = Column(Text, nullable=True)
    # Business date of the movement; NULL means "use created_at" (rows written
    # before this column existed). Production consumption carries the batch
    # date and packing carries the packing date, so reports stop dating a
    # backdated batch on the day it was typed in.
    movement_date = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    raw_material = relationship("RawMaterial", back_populates="stock_movements")
