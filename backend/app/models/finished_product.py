import enum

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import relationship

from backend.app.core.database import Base, _ist_wall


class FinishedProductMovementType(str, enum.Enum):
    PRODUCTION = "production"
    SALES = "sales"
    WASTAGE = "wastage"
    ADJUSTMENT = "adjustment"
    OPENING = "opening"
    RETURN = "return"


class FinishedProductStockMovement(Base):
    __tablename__ = "finished_product_stock_movements"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    packaging_size_id = Column(Integer, ForeignKey("packaging_sizes.id"), nullable=False)
    movement_type = Column(SQLEnum(FinishedProductMovementType), nullable=False)
    reference_type = Column(String(50), nullable=True)  # production, sales_challan, etc.
    reference_id = Column(Integer, nullable=True)
    quantity = Column(Numeric(15, 3), nullable=False)  # in kg
    packets = Column(Integer, default=0)
    rate_per_kg = Column(Numeric(15, 2), default=0)
    notes = Column(Text, nullable=True)
    # Business date of the movement (the day the stock actually moved). NULL
    # means "same day it was recorded" - every row written before this column
    # existed. Reports bucket by coalesce(movement_date, created_at), so a
    # batch packed days late still lands on its real packing day.
    movement_date = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    product = relationship("Product", back_populates="stock_movements")
