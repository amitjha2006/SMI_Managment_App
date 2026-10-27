import enum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import relationship

from backend.app.core.database import Base, _ist_wall


class ProductionStatus(str, enum.Enum):
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ProductionStage(str, enum.Enum):
    ROASTING = "roasting"
    GRINDING = "grinding"
    MIXING = "mixing"
    PACKAGING = "packaging"


class ProductWorkflowStage(Base):
    """The stages a product actually goes through, in order.

    Spec 6: "Workflow is configurable per product; a product may skip stages."
    The ProductionStage enum is the vocabulary of possible stages; this table
    records which of them a given product uses and in what sequence, so a
    product that is only blended and packed does not have to be recorded as
    passing through roasting and grinding.

    A product with NO rows here uses the full default sequence. That fallback
    is what makes this safe to introduce: every existing product keeps its
    current behaviour with no data migration, and a workflow is only stored
    once someone deliberately changes it.

    The delete-orphan cascade is what actually removes rows; ondelete="CASCADE"
    is declared for correctness on a real engine but is inert on SQLite, which
    this app does not run with PRAGMA foreign_keys=ON. The app soft-deletes
    products anyway, so a product row always survives to own its workflow.
    """

    __tablename__ = "product_workflow_stages"
    __table_args__ = (UniqueConstraint("product_id", "stage", name="uq_workflow_product_stage"),)

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage = Column(SQLEnum(ProductionStage), nullable=False)
    sequence = Column(Integer, nullable=False)  # 1-based, as entered
    created_at = Column(DateTime(timezone=True), default=_ist_wall)

    product = relationship("Product", back_populates="workflow_stages")


class ProductionEntry(Base):
    __tablename__ = "production_entries"

    id = Column(Integer, primary_key=True, index=True)
    production_number = Column(String(50), unique=True, index=True, nullable=False)
    production_date = Column(DateTime(timezone=True), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    # Nullable: a batch is ground in bulk, and which size(s) it goes into is
    # decided at packing time. Once packed this holds the first packed size so
    # list views keep showing one size per entry.
    packaging_size_id = Column(Integer, ForeignKey("packaging_sizes.id"), nullable=True)
    target_quantity = Column(Numeric(15, 3), default=0)  # in kg
    actual_output = Column(Numeric(15, 3), default=0)  # in kg (bulk kg until packed)
    # Business date of the packing run. NULL means produced-but-not-packed: the
    # wrapper and the finished stock are posted only when it is set.
    packed_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(SQLEnum(ProductionStatus), default=ProductionStatus.DRAFT)
    is_archived = Column(Boolean, default=False, nullable=False)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    archived_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)
    updated_at = Column(DateTime(timezone=True), onupdate=_ist_wall)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    product = relationship("Product", back_populates="production_entries")
    stage_entries = relationship(
        "ProductionStageEntry", back_populates="production_entry", cascade="all, delete-orphan"
    )
    wastages = relationship(
        "ProductionWastage", back_populates="production_entry", cascade="all, delete-orphan"
    )
    outputs = relationship(
        "ProductionOutput", back_populates="production_entry", cascade="all, delete-orphan"
    )


class ProductionConsumedMaterial(Base):
    """Materials booked against a production batch (deducted at save time)."""

    __tablename__ = "production_consumed_materials"

    id = Column(Integer, primary_key=True, index=True)
    production_entry_id = Column(
        Integer, ForeignKey("production_entries.id"), nullable=False, index=True
    )
    raw_material_id = Column(Integer, ForeignKey("raw_materials.id"), nullable=False)
    quantity_kg = Column(Numeric(15, 3), default=0)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)

    production_entry = relationship("ProductionEntry", backref="consumed_materials")
    raw_material = relationship("RawMaterial")


class ProductionOutput(Base):
    """One pack-size split of a production batch.

    A batch is processed in bulk and divided into pack sizes only at
    packaging, but one entry carries one batch number. Each row records how
    many packets of one size came out of the run and posts its own
    finished-stock movement. Entries created before multi-size support have
    no rows here; readers fall back to the entry's single packaging_size_id
    (their movement rows already exist).
    """

    __tablename__ = "production_outputs"

    id = Column(Integer, primary_key=True, index=True)
    production_entry_id = Column(
        Integer, ForeignKey("production_entries.id"), nullable=False, index=True
    )
    packaging_size_id = Column(Integer, ForeignKey("packaging_sizes.id"), nullable=False)
    packets = Column(Numeric(15, 3), default=0)  # packets packed in this size
    quantity_kg = Column(Numeric(15, 3), default=0)  # packets x pack weight
    created_at = Column(DateTime(timezone=True), default=_ist_wall)

    production_entry = relationship("ProductionEntry", back_populates="outputs")


class ProductionStageEntry(Base):
    __tablename__ = "production_stage_entries"

    id = Column(Integer, primary_key=True, index=True)
    production_entry_id = Column(Integer, ForeignKey("production_entries.id"), nullable=False)
    stage = Column(SQLEnum(ProductionStage), nullable=False)
    sequence = Column(Integer, nullable=False)
    input_quantity = Column(Numeric(15, 3), default=0)
    output_quantity = Column(Numeric(15, 3), default=0)
    wastage_quantity = Column(Numeric(15, 3), default=0)
    wastage_percentage = Column(Numeric(5, 2), default=0)
    # Packing gain (kg): output above input on a PACKAGING stage, where the
    # wrapper adds real weight. Tracked apart from wastage so a gain can never
    # cancel out true loss in report totals. Always 0 on other stages.
    packing_gain_kg = Column(Numeric(15, 3), default=0)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)

    production_entry = relationship("ProductionEntry", back_populates="stage_entries")


class ProductionWastage(Base):
    __tablename__ = "production_wastage"

    id = Column(Integer, primary_key=True, index=True)
    production_entry_id = Column(Integer, ForeignKey("production_entries.id"), nullable=False)
    stage = Column(SQLEnum(ProductionStage), nullable=False)
    raw_material_id = Column(Integer, ForeignKey("raw_materials.id"), nullable=True)
    wastage_quantity = Column(Numeric(15, 3), nullable=False)
    wastage_percentage = Column(Numeric(5, 2), default=0)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    production_entry = relationship("ProductionEntry", back_populates="wastages")
