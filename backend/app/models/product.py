from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.orm import relationship

from backend.app.core.database import Base, _ist_wall


class ProductCategory(Base):
    """A real master list for product categories.

    Products were the last holdout using free text, which fragments silently
    ("Dry Masala" vs "dry masala" become two different categories in reports).
    Expenses and raw materials already have proper category tables, so this
    makes products consistent. parent_id is supported for future nesting and is
    used flat for now, matching expense_categories.
    """

    __tablename__ = "product_categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(80), unique=True, index=True, nullable=False)
    description = Column(Text, nullable=True)
    parent_id = Column(Integer, ForeignKey("product_categories.id"), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)
    updated_at = Column(DateTime(timezone=True), onupdate=_ist_wall)

    products = relationship("Product", back_populates="category_rel")
    parent = relationship("ProductCategory", remote_side="ProductCategory.id")


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(String(100), nullable=False)
    category_id = Column(Integer, ForeignKey("product_categories.id"), nullable=True)
    description = Column(Text, nullable=True)
    base_rate_per_kg = Column(Numeric(15, 2), default=0)
    # GST percentage for this product, e.g. 5.00 for 5% (not 0.05).
    #
    # Stored per product rather than globally because a masala range commonly
    # mixes rates across lines on a single invoice, and GST has to be extracted
    # per line - a single company rate cannot split a mixed invoice correctly.
    #
    # Default 0 means "not yet set". That is deliberately NOT a valid rate for a
    # tax invoice, so sales_invoice creation refuses to guess one: it blocks
    # rather than silently invoicing at the wrong rate. See
    # services/gst.py for the inclusive-amount extraction this feeds.
    gst_rate = Column(Numeric(5, 2), default=0, nullable=False)
    # HSN/SAC code printed on a tax invoice. Blankable on purpose: the codes
    # are the business's to supply, and a wrong code is worse than a missing
    # one. Invoice creation warns rather than blocks, so a missing code cannot
    # silently produce a non-compliant invoice the user believes is fine.
    hsn_code = Column(String(12), nullable=True)
    # Free-text help/recipe kept manually per product (formulation in plain
    # words, process notes, anything else). Independent document, shown while
    # producing. Nullable: most products simply have none.
    help_text = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)
    updated_at = Column(DateTime(timezone=True), onupdate=_ist_wall)

    category_rel = relationship("ProductCategory", back_populates="products")

    @property
    def category(self):
        """The category NAME, so callers and the API keep seeing a plain string
        even though storage is now a proper reference."""
        return self.category_rel.name if self.category_rel else None

    packaging_sizes = relationship(
        "PackagingSize",
        back_populates="product",
        cascade="all, delete-orphan",
        # Lighter sizes first (50g, 100g, 250g, …) everywhere the sizes are
        # shown — pack-size dropdowns, the stock breakdown, the editors.
        order_by="PackagingSize.weight_in_kg, PackagingSize.size_name",
    )
    workflow_stages = relationship(
        "ProductWorkflowStage",
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductWorkflowStage.sequence",
    )
    formulation_items = relationship(
        "ProductFormulation",
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductFormulation.sequence",
    )
    formulation_steps = relationship(
        "ProductFormulationStep",
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductFormulationStep.sequence",
    )
    production_entries = relationship("ProductionEntry", back_populates="product")
    sales_items = relationship("SalesChallanItem", back_populates="product")
    stock_movements = relationship("FinishedProductStockMovement", back_populates="product")


class PackagingSize(Base):
    __tablename__ = "packaging_sizes"
    # One row per size name per product - among its LIVE sizes. The request
    # handler already skips a size the product has, but two concurrent
    # "add 100g" requests both read the list before either committed and both
    # inserted (the duplicate Cumin 100g row). Partial on is_active because an
    # inactive duplicate is legitimate history: the old merge left deactivated
    # twins behind, and re-adding the name restores that row.
    __table_args__ = (
        Index(
            "uq_pack_size_per_product",
            "product_id",
            "size_name",
            unique=True,
            sqlite_where=text("is_active = 1"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    size_name = Column(String(50), nullable=False)  # e.g., "50g", "100g", "Loose Powder"
    weight_in_kg = Column(Numeric(10, 5), nullable=False)  # weight per packet in kg
    packets_per_unit = Column(Integer, default=1)  # for loose powder = 1
    rate_per_kg = Column(Numeric(15, 2), default=0)
    # Weight of one empty pouch in kg (differs per size). Lets a production
    # batch account the wrapper on the input side: wrapper used =
    # packets x this weight, consumed from a wrapper-roll raw material.
    wrapper_weight_kg = Column(Numeric(10, 5), default=0)
    # Which raw material this size's pouches are made from - a printed film
    # roll, stocked in kg like any raw material. Per size because a product's
    # 50g and 250g use different film. Nullable: unmapped sizes fall back to
    # picking a roll on the production form.
    wrapper_material_id = Column(Integer, ForeignKey("raw_materials.id"), nullable=True)
    # Low-stock threshold in PACKETS (e.g. warn below 200 packets of 250g).
    # Packs are counted in packets everywhere in the UI; 0 (default) alerts
    # only when the size is fully out, same spirit as raw thresholds.
    threshold_packets = Column(Numeric(15, 3), default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)
    updated_at = Column(DateTime(timezone=True), onupdate=_ist_wall)

    product = relationship("Product", back_populates="packaging_sizes")
    sales_items = relationship("SalesChallanItem", back_populates="packaging_size")
    rate_history = relationship(
        "SizeRateHistory", back_populates="packaging_size", cascade="all, delete-orphan"
    )


class ProductFormulation(Base):
    """One ingredient line of a product's mix ratio (formulation).

    Percentages across a product's lines must total 100. The formulation is
    a reference for mixing and for prefilling production consumption - it
    never moves stock by itself. Deleting a product removes its lines;
    a deleted raw material leaves the line unreadable (shown as missing)
    rather than silently shifting the other shares.
    """

    __tablename__ = "product_formulations"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    raw_material_id = Column(Integer, ForeignKey("raw_materials.id"), nullable=False)
    percentage = Column(Numeric(5, 2), nullable=False)
    sequence = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)

    product = relationship("Product", back_populates="formulation_items")
    raw_material = relationship("RawMaterial")


class ProductFormulationStep(Base):
    """One ordered processing step for a product's formulation.

    Free-text instruction ("Roast at 120C for 20 min, then cool"), tagged
    with an optional production stage. Guidance for the person making the
    batch - it drives no calculation and moves no stock.
    """

    __tablename__ = "product_formulation_steps"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    stage = Column(String(20), nullable=True)
    sequence = Column(Integer, default=1, nullable=False)
    instruction = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)

    product = relationship("Product", back_populates="formulation_steps")


class SizeRateHistory(Base):
    """Every rate change on a pack size: old -> new, when, by whom.

    Movements keep the rate they were valued at, so history here never
    restates stock - it only explains why margins moved.
    """

    __tablename__ = "size_rate_history"

    id = Column(Integer, primary_key=True, index=True)
    packaging_size_id = Column(
        Integer, ForeignKey("packaging_sizes.id"), nullable=False, index=True
    )
    old_rate = Column(Numeric(15, 2), nullable=False)
    new_rate = Column(Numeric(15, 2), nullable=False)
    changed_at = Column(DateTime(timezone=True), default=_ist_wall)
    changed_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    packaging_size = relationship("PackagingSize", back_populates="rate_history")
