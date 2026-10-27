import enum
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_serializer


# Enums
class UserRole(str, enum.Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    DATA_ENTRY = "data_entry"
    VIEWER = "viewer"
    CHALLAN_ENTRY = "challan_entry"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    PARTIAL = "partial"
    PAID = "paid"
    CANCELLED = "cancelled"


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


class ChallanStatus(str, enum.Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    CLOSED = "closed"
    DISPATCHED = "dispatched"
    DELIVERED = "delivered"


class ExpenseCategoryType(str, enum.Enum):
    ELECTRICITY = "Electricity"
    GAS_FUEL = "Gas/Fuel"
    RAW_MATERIAL = "Raw Material"
    PACKAGING = "Packaging"
    TRANSPORT = "Transport"
    MAINTENANCE = "Maintenance"
    RENT = "Rent"
    SALARY = "Salary"
    ADVANCE = "Advance"
    MISCELLANEOUS = "Miscellaneous"


# User schemas
class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: str | None = None
    full_name: str | None = None
    role: UserRole = UserRole.VIEWER


class UserCreate(UserBase):
    password: str = Field(..., min_length=6)


class UserUpdate(BaseModel):
    username: str | None = Field(None, min_length=3, max_length=50)
    email: str | None = None
    full_name: str | None = None
    role: UserRole | None = None
    is_active: bool | None = None


class UserSchema(UserBase):
    id: int
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Token schemas
class TokenSchema(BaseModel):
    access_token: str
    token_type: str = "bearer"
    # Returned by /auth/editor-mode so the client knows the resulting state
    # without having to decode the token.
    editor_mode: bool | None = None


# Customer schemas
class CustomerBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    company_name: str | None = Field(None, max_length=100)
    gst_no: str | None = Field(None, max_length=15)
    pan_no: str | None = Field(None, max_length=10)
    contact_number: str = Field(..., min_length=5, max_length=15)
    contact_number_2: str | None = Field(None, max_length=15)
    email: str | None = Field(None, max_length=100)
    address: str | None = Field(None)
    account_no: str | None = Field(None, max_length=50)
    ifsc_code: str | None = Field(None, max_length=11)
    bank_detail: str | None = Field(None)
    cash: Decimal | None = Field(None, ge=0)
    notes: str | None = Field(None)
    opening_balance: Decimal | None = Field(0, ge=0)
    credit_limit: Decimal | None = Field(0, ge=0)


class CustomerCreate(CustomerBase):
    pass


class CustomerUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    company_name: str | None = Field(None, max_length=100)
    gst_no: str | None = Field(None, max_length=15)
    pan_no: str | None = Field(None, max_length=10)
    contact_number: str | None = Field(None, min_length=5, max_length=15)
    contact_number_2: str | None = Field(None, max_length=15)
    email: str | None = Field(None, max_length=100)
    address: str | None = Field(None)
    account_no: str | None = Field(None, max_length=50)
    ifsc_code: str | None = Field(None, max_length=11)
    bank_detail: str | None = Field(None)
    cash: Decimal | None = Field(None, ge=0)
    notes: str | None = Field(None)
    opening_balance: Decimal | None = Field(None)
    credit_limit: Decimal | None = Field(None, ge=0)


class CustomerSchema(CustomerBase):
    id: int
    customer_id: str
    # Reads tolerate legacy/imported rows stored with an empty contact;
    # entry-time strictness lives on CustomerCreate (min_length=5). Without
    # this, one such row 500s the whole list endpoint ("Failed to load").
    contact_number: str = Field(..., max_length=15)
    outstanding_balance: Decimal
    opening_balance: Decimal
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Supplier schemas
class SupplierBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    company_name: str | None = Field(None, max_length=100)
    gst_no: str | None = Field(None, max_length=15)
    pan_no: str | None = Field(None, max_length=10)
    contact_number: str = Field(..., min_length=5, max_length=15)
    contact_number_2: str | None = Field(None, max_length=15)
    email: str | None = Field(None, max_length=100)
    address: str | None = Field(None)
    account_no: str | None = Field(None, max_length=50)
    ifsc_code: str | None = Field(None, max_length=11)
    bank_detail: str | None = Field(None)
    cash: Decimal | None = Field(None, ge=0)
    notes: str | None = Field(None)
    opening_balance: Decimal | None = Field(0, ge=0)


class SupplierCreate(SupplierBase):
    pass


class SupplierUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    company_name: str | None = Field(None, max_length=100)
    gst_no: str | None = Field(None, max_length=15)
    pan_no: str | None = Field(None, max_length=10)
    contact_number: str | None = Field(None, min_length=5, max_length=15)
    contact_number_2: str | None = Field(None, max_length=15)
    email: str | None = Field(None, max_length=100)
    address: str | None = Field(None)
    account_no: str | None = Field(None, max_length=50)
    ifsc_code: str | None = Field(None, max_length=11)
    bank_detail: str | None = Field(None)
    cash: Decimal | None = Field(None, ge=0)
    notes: str | None = Field(None)
    opening_balance: Decimal | None = Field(None)


class SupplierSchema(SupplierBase):
    id: int
    supplier_id: str
    # Same tolerance as CustomerSchema: imports may store "" contact, and a
    # strict read schema turns one legacy row into a broken list endpoint.
    contact_number: str = Field(..., max_length=15)
    outstanding_balance: Decimal
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Raw material schemas
class UnitType(str, enum.Enum):
    KG = "kg"
    GRAM = "gram"
    PACKET = "packet"
    PIECES = "pieces"
    LITRES = "litres"
    METRES = "metres"


class RawMaterialCategoryBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    description: str | None = None


class RawMaterialCategoryCreate(RawMaterialCategoryBase):
    pass


class RawMaterialCategoryRawMaterialCategory(RawMaterialCategoryBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RawMaterialBase(BaseModel):
    material_id: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=100)
    category_id: int | None = Field(None)
    unit: UnitType = UnitType.KG
    opening_stock: Decimal = Field(0, ge=0)
    threshold_stock: Decimal = Field(0, ge=0)
    reorder_level: Decimal = Field(0, ge=0)
    purchase_rate: Decimal = Field(0, ge=0)
    hsn_code: str | None = Field(None, max_length=12)
    is_active: bool = True
    watch_stock: bool = True


class RawMaterialCreate(RawMaterialBase):
    pass


class RawMaterialUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    category_id: int | None = Field(None)
    unit: UnitType | None = Field(None)
    opening_stock: Decimal | None = Field(None, ge=0)
    threshold_stock: Decimal | None = Field(None, ge=0)
    reorder_level: Decimal | None = Field(None, ge=0)
    purchase_rate: Decimal | None = Field(None, ge=0)
    hsn_code: str | None = Field(None, max_length=12)
    is_active: bool | None = Field(None)
    watch_stock: bool | None = Field(None)


class RawMaterialSchema(RawMaterialBase):
    id: int
    current_stock: Decimal
    is_active: bool
    created_at: datetime
    category_name: str | None = None

    model_config = ConfigDict(from_attributes=True)


# Purchase invoice schemas
class PaymentStatusSchema(str, enum.Enum):
    PENDING = "pending"
    PARTIAL = "partial"
    PAID = "paid"
    CANCELLED = "cancelled"


class PurchaseInvoiceItemBase(BaseModel):
    raw_material_id: int = Field(..., ge=1)
    quantity: Decimal = Field(..., ge=0)
    unit: UnitType = Field(...)
    rate: Decimal = Field(..., ge=0)
    gst_percent: Decimal = Field(0, ge=0)
    cgst_amount: Decimal = Field(0, ge=0)
    sgst_amount: Decimal = Field(0, ge=0)
    igst_amount: Decimal = Field(0, ge=0)
    amount: Decimal = Field(..., ge=0)
    # Line's pro-rata share of the invoice's transport / other charges,
    # plus the derived landed cost per kg (see create_purchase_invoice).
    # Defaults are Decimal("0") (not int 0): when a response path ever falls
    # back to the default, pydantic would otherwise warn while serializing an
    # int into a decimal field.
    transport_amount: Decimal = Field(Decimal("0"), ge=0)
    other_amount: Decimal = Field(Decimal("0"), ge=0)
    transport_per_kg: Decimal = Field(Decimal("0"), ge=0)
    other_per_kg: Decimal = Field(Decimal("0"), ge=0)
    total_cost_per_kg: Decimal = Field(Decimal("0"), ge=0)


class PurchaseInvoiceItemCreate(PurchaseInvoiceItemBase):
    pass


class PurchaseInvoiceItemSchema(PurchaseInvoiceItemBase):
    id: int
    created_at: datetime
    # Resolved for display (same precedent as supplier_name above): the
    # detail view must show the material name, not just its id.
    material_name: str | None = None

    model_config = ConfigDict(from_attributes=True)


class PurchaseInvoiceBase(BaseModel):
    invoice_number: str = Field(..., min_length=1, max_length=50)
    invoice_date: datetime
    supplier_id: int = Field(..., ge=1)
    gst_percent: Decimal = Field(0, ge=0)
    other_charges: Decimal = Field(0, ge=0)
    # Merchant-side costing only (freight etc.): informational, never part of
    # the payable total or supplier dues.
    transport_charges: Decimal = Field(0, ge=0)
    discount: Decimal = Field(0, ge=0)
    notes: str | None = Field(None)


class PurchaseInvoiceCreate(PurchaseInvoiceBase):
    items: list[PurchaseInvoiceItemCreate]


class PurchaseInvoiceSchema(PurchaseInvoiceBase):
    id: int
    total_amount: Decimal
    paid_amount: Decimal
    outstanding_amount: Decimal
    payment_status: PaymentStatusSchema
    is_confirmed: bool
    created_at: datetime
    supplier_name: str | None = None
    supplier_company: str | None = None
    items: list[PurchaseInvoiceItemSchema] = []
    is_archived: bool = False
    archived_at: datetime | None = None
    archived_by: int | None = None

    model_config = ConfigDict(from_attributes=True)


# Supplier payment schemas
class SupplierPaymentBase(BaseModel):
    purchase_invoice_id: int = Field(..., ge=1)
    payment_date: datetime
    amount: Decimal = Field(..., ge=0)
    payment_mode: str = Field(..., max_length=30)  # cash, upi, bank_transfer, cheque, card, other
    transaction_ref: str | None = Field(None, max_length=100)
    notes: str | None = Field(None)


class SupplierPaymentCreate(SupplierPaymentBase):
    pass


class SupplierPaymentSchema(SupplierPaymentBase):
    id: int
    invoice_number: str | None = None
    outstanding_after: Decimal | None = None
    created_at: datetime | None = None
    is_archived: bool = False
    archived_at: datetime | None = None
    archived_by: int | None = None

    model_config = ConfigDict(from_attributes=True)


# Product schemas
class PackagingSizeBase(BaseModel):
    size_name: str = Field(..., min_length=1, max_length=50)
    weight_in_kg: Decimal = Field(..., gt=0)
    rate_per_kg: Decimal = Field(0, ge=0)
    packets_per_unit: int | None = Field(1, ge=1)
    threshold_packets: Decimal = Field(0, ge=0)
    wrapper_weight_kg: Decimal = Field(0, ge=0)
    # Which raw material (a printed film roll) this size's pouches are made
    # from. Nullable: most sizes start unmapped, and production falls back to
    # an explicit picker until someone chooses one.
    wrapper_material_id: int | None = Field(None, ge=1)


class PackagingSizeCreate(PackagingSizeBase):
    pass


class PackagingSizeUpdate(BaseModel):
    size_name: str | None = Field(None, min_length=1, max_length=50)
    weight_in_kg: Decimal | None = Field(None, gt=0)
    rate_per_kg: Decimal | None = Field(None, ge=0)
    packets_per_unit: int | None = Field(None, ge=1)
    threshold_packets: Decimal | None = Field(None, ge=0)
    wrapper_weight_kg: Decimal | None = Field(None, ge=0)
    wrapper_material_id: int | None = Field(None, ge=1)


class PackagingSizeSchema(PackagingSizeBase):
    id: int
    product_id: int
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SizeRateHistorySchema(BaseModel):
    id: int
    packaging_size_id: int
    old_rate: Decimal
    new_rate: Decimal
    changed_at: datetime | None = None
    changed_by: int | None = None
    changed_by_name: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ProductCategorySchema(BaseModel):
    id: int
    name: str
    description: str | None = None
    parent_id: int | None = None
    parent_name: str | None = None
    is_active: bool = True
    product_count: int | None = None

    model_config = ConfigDict(from_attributes=True)


class ProductCategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: str | None = None
    parent_id: int | None = None
    is_active: bool = True


class PrintFooterSchema(BaseModel):
    id: int
    name: str
    body: str | None = None
    is_active: bool = True
    challan_count: int | None = None

    model_config = ConfigDict(from_attributes=True)


class PrintFooterCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    body: str | None = None
    is_active: bool = True


class ProductBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    # Accepted for convenience; the server resolves it to (or creates) a real
    # product_categories row. category_id is the preferred form.
    category: str | None = Field(None, max_length=50)
    category_id: int | None = None
    description: str | None = None
    base_rate_per_kg: Decimal = Field(0, ge=0)
    # GST percentage (5 means 5%, not 0.05). 0 means "not configured", which
    # sales-invoice creation refuses rather than guessing. Capped at 100 with
    # the upper bound exclusive via the service's own check, because 100% would
    # make the inclusive-amount extraction divide by 2 for no real slab.
    gst_rate: Decimal = Field(0, ge=0, lt=100)
    # HSN/SAC code printed on a tax invoice. Optional - the codes are the
    # business's to supply, and invoice creation warns rather than blocks.
    hsn_code: str | None = Field(None, max_length=12)
    formulation: str | None = None


class ProductCreate(ProductBase):
    packaging_sizes: list[PackagingSizeCreate]


class ProductUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    category: str | None = Field(None, max_length=50)
    category_id: int | None = None
    description: str | None = None
    base_rate_per_kg: Decimal | None = Field(None, ge=0)
    gst_rate: Decimal | None = Field(None, ge=0, lt=100)
    hsn_code: str | None = Field(None, max_length=12)
    is_active: bool | None = Field(None)


class ProductSchema(ProductBase):
    id: int
    product_id: str
    is_active: bool
    created_at: datetime
    packaging_sizes: list[PackagingSizeSchema] = []
    # Set by POST /products so the UI can explain what happened when a name
    # matched an existing product and the weight was added to it instead.
    sizes_added: list[str] | None = None
    sizes_skipped: list[str] | None = None
    merged_into_existing: bool | None = None

    model_config = ConfigDict(from_attributes=True)


# Production schemas
class ProductionStageBase(BaseModel):
    stage: ProductionStage = Field(...)
    sequence: int = Field(..., ge=1)
    input_quantity: Decimal = Field(0, ge=0)
    output_quantity: Decimal = Field(0, ge=0)
    wastage_percentage: Decimal = Field(0, ge=0, le=100)
    notes: str | None = Field(None)


class ProductionStageCreate(ProductionStageBase):
    pass


class ProductionStageUpdate(BaseModel):
    stage: ProductionStage | None = Field(None)
    sequence: int | None = Field(None, ge=1)
    input_quantity: Decimal | None = Field(None, ge=0)
    output_quantity: Decimal | None = Field(None, ge=0)
    wastage_percentage: Decimal | None = Field(None, ge=0, le=100)
    notes: str | None = Field(None)


class ProductionStageSchema(ProductionStageBase):
    id: int
    production_entry_id: int
    # Packing gain (kg) on packaging stages; 0 elsewhere. Stored, not
    # derived, so the detail view shows what was actually recorded.
    packing_gain_kg: Decimal = Field(0, ge=0)

    model_config = ConfigDict(from_attributes=True)


class ProductionOutputSchema(BaseModel):
    id: int
    production_entry_id: int
    packaging_size_id: int
    packets: Decimal = Field(0, ge=0)
    quantity_kg: Decimal = Field(0, ge=0)
    packaging_size_name: str | None = None
    pack_weight_kg: float | None = None

    model_config = ConfigDict(from_attributes=True)


class ProductionEntryBase(BaseModel):
    production_number: str = Field(..., min_length=1, max_length=50)
    production_date: datetime
    product_id: int = Field(..., ge=1)
    # Null until the batch is packed: the size is chosen at packing time.
    packaging_size_id: int | None = Field(None, ge=1)
    target_quantity: Decimal = Field(0, ge=0)
    notes: str | None = Field(None)


class ProductionEntryCreate(ProductionEntryBase):
    stages: list[ProductionStageCreate]


class ProductionEntryUpdate(BaseModel):
    production_number: str | None = Field(None, min_length=1, max_length=50)
    production_date: datetime | None = Field(None)
    product_id: int | None = Field(None, ge=1)
    packaging_size_id: int | None = Field(None, ge=1)
    target_quantity: Decimal | None = Field(None, ge=0)
    status: ProductionStatus | None = Field(None)
    notes: str | None = Field(None)


class ProductionEntrySchema(ProductionEntryBase):
    id: int
    actual_output: Decimal
    status: ProductionStatus
    packed_at: datetime | None = None
    created_at: datetime
    product_name: str | None = None
    packaging_size_name: str | None = None
    pack_weight_kg: float | None = None
    actual_packets: float | None = None
    stage_entries: list[ProductionStageSchema] = []
    outputs: list[ProductionOutputSchema] = []
    # Quoted because ProductionWastageSchema is defined further down this file.
    # Pydantic resolves the string against the module namespace, so the order
    # stops mattering; unquoted it only worked by accident of how pydantic
    # happens to defer annotation evaluation today.
    wastages: list["ProductionWastageSchema"] = []
    is_archived: bool = False
    archived_at: datetime | None = None
    archived_by: int | None = None

    model_config = ConfigDict(from_attributes=True)


class ProductionWastageBase(BaseModel):
    stage: ProductionStage = Field(...)
    raw_material_id: int | None = Field(None)
    wastage_quantity: Decimal = Field(..., ge=0)
    wastage_percentage: Decimal = Field(0, ge=0, le=100)
    notes: str | None = Field(None)


class ProductionWastageCreate(ProductionWastageBase):
    pass


class ProductionWastageSchema(ProductionWastageBase):
    id: int
    production_entry_id: int

    model_config = ConfigDict(from_attributes=True)


# Finished product schemas
class FinishedProductMovementType(str, enum.Enum):
    PRODUCTION = "production"
    SALES = "sales"
    WASTAGE = "wastage"
    ADJUSTMENT = "adjustment"
    OPENING = "opening"
    RETURN = "return"


class FinishedProductStockMovementBase(BaseModel):
    product_id: int = Field(..., ge=1)
    packaging_size_id: int = Field(..., ge=1)
    movement_type: FinishedProductMovementType = Field(...)
    reference_type: str | None = Field(None)
    reference_id: int | None = Field(None)
    quantity: Decimal = Field(..., ge=0)
    packets: int | None = Field(0, ge=0)
    rate_per_kg: Decimal = Field(0, ge=0)
    notes: str | None = Field(None)


class FinishedProductStockMovementCreate(FinishedProductStockMovementBase):
    pass


class FinishedProductStockMovementSchema(FinishedProductStockMovementBase):
    id: int
    created_at: datetime
    product_name: str
    packaging_size_name: str

    model_config = ConfigDict(from_attributes=True)


# Sales challan schemas
class SalesChallanItemBase(BaseModel):
    product_id: int = Field(..., ge=1)
    packaging_size_id: int | None = Field(None)
    quantity: Decimal = Field(..., ge=0)  # quantity in packets/units
    weight: Decimal = Field(..., ge=0)  # weight in kg
    rate_per_kg: Decimal = Field(..., ge=0)
    amount: Decimal = Field(..., ge=0)


class SalesChallanItemCreate(SalesChallanItemBase):
    pass


class SalesChallanItemSchema(SalesChallanItemBase):
    id: int
    # Frozen at creation so a later product rename cannot alter an issued
    # challan. Optional because rows predating the snapshot are still NULL.
    product_name: str | None = None
    size_name: str | None = None

    model_config = ConfigDict(from_attributes=True)


class SalesChallanBase(BaseModel):
    challan_date: datetime
    customer_id: int = Field(..., ge=1)
    notes: str | None = Field(None)
    dispatched_at: datetime | None = Field(None)
    delivered_at: datetime | None = Field(None)
    transporter: str | None = Field(None, max_length=100)
    vehicle_no: str | None = Field(None, max_length=50)


class SalesChallanCreate(SalesChallanBase):
    items: list[SalesChallanItemCreate]


class SalesChallanSchema(SalesChallanBase):
    id: int
    challan_number: str
    status: ChallanStatus
    total_weight: Decimal
    total_price: Decimal
    paid_amount: Decimal
    outstanding_amount: Decimal
    customer_name: str | None = None
    # Frozen print header - see services/challan_snapshot.py. `stamped` lets the
    # UI explain a blank header rather than silently printing nothing.
    company_name: str | None = None
    company_address: str | None = None
    company_gstin: str | None = None
    company_phone: str | None = None
    company_email: str | None = None
    company_terms: str | None = None
    cust_company_name: str | None = None
    cust_address: str | None = None
    cust_gst_no: str | None = None
    cust_contact_number: str | None = None
    company_snapshot_at: datetime | None = None
    stamped: bool = False
    # Server-rendered so the printed wording is defined in one place.
    total_in_words: str | None = None
    items: list[SalesChallanItemSchema]
    created_at: datetime
    is_archived: bool = False
    archived_at: datetime | None = None
    archived_by: int | None = None

    model_config = ConfigDict(from_attributes=True)


# Sales Invoice schemas
class SalesInvoiceItemBase(BaseModel):
    product_id: int = Field(..., ge=1)
    packaging_size_id: int = Field(..., ge=1)
    quantity: int = Field(..., ge=1)
    weight: Decimal = Field(..., ge=0)
    rate_per_kg: Decimal = Field(..., ge=0)
    amount: Decimal = Field(..., ge=0)


class SalesInvoiceItemCreate(SalesInvoiceItemBase):
    pass


class SalesInvoiceItemSchema(SalesInvoiceItemBase):
    id: int
    sales_invoice_id: int

    model_config = ConfigDict(from_attributes=True)


class SalesInvoiceBase(BaseModel):
    invoice_number: str
    invoice_date: datetime
    challan_id: int = Field(..., ge=1)
    customer_id: int = Field(..., ge=1)
    intra_state: bool = True
    total_quantity: int = Field(..., ge=0)
    total_weight: Decimal = Field(..., ge=0)
    taxable_value: Decimal = Field(..., ge=0)
    total_tax: Decimal = Field(..., ge=0)
    cgst: Decimal = Field(..., ge=0)
    sgst: Decimal = Field(..., ge=0)
    igst: Decimal = Field(..., ge=0)
    grand_total: Decimal = Field(..., ge=0)


class SalesInvoiceCreate(SalesInvoiceBase):
    items: list[SalesInvoiceItemCreate]


class SalesInvoiceSchema(SalesInvoiceBase):
    id: int
    items: list[SalesInvoiceItemSchema] = []
    created_at: datetime
    is_archived: bool = False
    archived_at: datetime | None = None
    archived_by: int | None = None

    model_config = ConfigDict(from_attributes=True)


# Customer payment schemas
class CustomerPaymentBase(BaseModel):
    challan_id: int = Field(..., ge=1)
    payment_date: datetime
    amount: Decimal = Field(..., ge=0)
    payment_mode: str = Field(..., max_length=30)  # cash, upi, bank_transfer, cheque, card, other
    transaction_ref: str | None = Field(None, max_length=100)
    notes: str | None = Field(None)


class CustomerPaymentCreate(CustomerPaymentBase):
    pass


class CustomerPaymentSchema(CustomerPaymentBase):
    id: int
    challan_number: str | None = None
    outstanding_after: Decimal | None = None
    created_at: datetime | None = None
    is_archived: bool = False
    archived_at: datetime | None = None
    archived_by: int | None = None

    model_config = ConfigDict(from_attributes=True)


# Expense schemas
class ExpenseCategorySchema(BaseModel):
    id: int
    name: str
    description: str | None = None
    is_active: bool
    parent_id: int | None = None

    model_config = ConfigDict(from_attributes=True)


class ProductCategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    description: str | None = None
    parent_id: int | None = None


class ProductCategoryUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=50)
    description: str | None = None
    is_active: bool | None = None
    parent_id: int | None = None


class ExpenseBase(BaseModel):
    category_id: int = Field(..., ge=1)
    description: str = Field(..., min_length=1)
    amount: Decimal = Field(..., ge=0)
    date: datetime = Field(...)
    payment_mode: str = Field(..., max_length=30)  # cash, upi, bank_transfer, cheque, card, other
    payee: str | None = Field(None, max_length=100)
    notes: str | None = Field(None)


class ExpenseCreate(ExpenseBase):
    pass


class ExpenseSchema(ExpenseBase):
    id: int
    category_name: str | None = None
    created_at: datetime | None = None
    is_archived: bool = False
    archived_at: datetime | None = None
    archived_by: int | None = None

    model_config = ConfigDict(from_attributes=True)


# Employee schemas
class EmployeeStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ON_LEAVE = "on_leave"


class EmployeeType(str, enum.Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACTOR = "contractor"


class EmployeeBase(BaseModel):
    employee_id: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=100)
    email: str | None = Field(None, max_length=100)
    phone: str | None = Field(None, max_length=15)
    address: str | None = Field(None)
    employee_type: EmployeeType = Field(...)
    status: EmployeeStatus = Field(default=EmployeeStatus.ACTIVE)
    joining_date: datetime | None = Field(None)
    salary: Decimal = Field(0, ge=0)
    bank_account_no: str | None = Field(None, max_length=50)
    ifsc_code: str | None = Field(None, max_length=11)
    pan_no: str | None = Field(None, max_length=10)
    aadhaar_no: str | None = Field(None, max_length=12)
    emergency_contact: str | None = Field(None, max_length=15)
    emergency_name: str | None = Field(None, max_length=100)
    notes: str | None = Field(None)


class EmployeeCreate(EmployeeBase):
    pass


class EmployeeUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    email: str | None = Field(None, max_length=100)
    phone: str | None = Field(None, max_length=15)
    address: str | None = Field(None)
    employee_type: EmployeeType | None = Field(None)
    status: EmployeeStatus | None = Field(None)
    salary: Decimal | None = Field(None, ge=0)
    notes: str | None = Field(None)


class EmployeeSchema(EmployeeBase):
    id: int
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


# Attendance schemas
class AttendanceBase(BaseModel):
    employee_id: int = Field(..., ge=1)
    date: datetime = Field(...)
    check_in: str | None = Field(None, max_length=10)
    check_out: str | None = Field(None, max_length=10)
    hours_worked: Decimal | None = Field(None, ge=0)
    status: str | None = Field(None, max_length=20)


class AttendanceCreate(AttendanceBase):
    pass


class AttendanceSchema(AttendanceBase):
    id: int

    model_config = ConfigDict(from_attributes=True)


# Leave schemas
class LeaveBase(BaseModel):
    employee_id: int = Field(..., ge=1)
    start_date: datetime = Field(...)
    end_date: datetime = Field(...)
    leave_type: str = Field(..., max_length=30)  # casual, sick, earned, etc.
    days: Decimal = Field(..., ge=0)
    reason: str | None = Field(None)
    status: str = Field(default="pending")


class LeaveCreate(LeaveBase):
    pass


class LeaveSchema(LeaveBase):
    id: int

    model_config = ConfigDict(from_attributes=True)


# Salary payment schemas
class SalaryPaymentBase(BaseModel):
    employee_id: int = Field(..., ge=1)
    payment_date: datetime = Field(...)
    basic_salary: Decimal = Field(0, ge=0)
    allowances: Decimal = Field(0, ge=0)
    deductions: Decimal = Field(0, ge=0)
    net_pay: Decimal = Field(0, ge=0)
    notes: str | None = Field(None)


class SalaryPaymentCreate(SalaryPaymentBase):
    pass


class SalaryPaymentSchema(SalaryPaymentBase):
    id: int
    employee_name: str

    model_config = ConfigDict(from_attributes=True)


# Employee advance schemas
class EmployeeAdvanceBase(BaseModel):
    employee_id: int = Field(..., ge=1)
    advance_amount: Decimal = Field(..., ge=0)
    given_date: datetime = Field(...)
    return_date: datetime | None = Field(None)
    outstanding: Decimal = Field(0, ge=0)
    notes: str | None = Field(None)


class EmployeeAdvanceCreate(EmployeeAdvanceBase):
    pass


class EmployeeAdvanceSchema(EmployeeAdvanceBase):
    id: int

    model_config = ConfigDict(from_attributes=True)


# Deduction schemas
class DeductionBase(BaseModel):
    employee_id: int = Field(..., ge=1)
    deduction_type: str = Field(..., max_length=30)  # tax, fine, other
    amount: Decimal = Field(0, ge=0)
    date: datetime = Field(...)
    reason: str | None = Field(None)
    notes: str | None = Field(None)


class DeductionCreate(DeductionBase):
    pass


class DeductionSchema(DeductionBase):
    id: int

    model_config = ConfigDict(from_attributes=True)


# Notification schemas
class NotificationType(str, enum.Enum):
    LOW_STOCK = "low_stock"
    DELIVERY_TARGET = "delivery_target"
    NEW_USER = "new_user"
    SYSTEM = "system"


class NotificationRuleBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    notification_type: NotificationType = Field(...)
    target_type: str = Field(...)  # raw_material, product, customer, etc.
    target_id: int | None = Field(None)
    is_enabled: bool = Field(True)
    provider: str | None = Field(None, max_length=50)  # email, whatsapp, sms
    recipient: str | None = Field(None, max_length=200)
    template: str | None = Field(None)
    threshold_value: Decimal = Field(0, ge=0)


class NotificationRuleCreate(NotificationRuleBase):
    pass


class NotificationRuleSchema(NotificationRuleBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationLogBase(BaseModel):
    rule_id: int | None = Field(None)
    recipient: str = Field(...)
    message: str = Field(...)
    status: str = Field(...)  # sent, failed, pending
    error_message: str | None = Field(None)


class NotificationLogSchema(NotificationLogBase):
    id: int
    sent_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Attachment schemas
class AttachmentBase(BaseModel):
    file_name: str = Field(..., min_length=1, max_length=200)
    file_path: str = Field(..., min_length=1, max_length=500)
    file_type: str | None = Field(None, max_length=100)
    file_size: int | None = Field(None, ge=0)
    related_type: str = Field(...)  # customer, supplier, invoice, production, etc.
    related_id: int | None = Field(None)
    description: str | None = Field(None)


class AttachmentCreate(AttachmentBase):
    pass


class AttachmentSchema(AttachmentBase):
    id: int
    uploaded_at: datetime
    uploader_name: str

    model_config = ConfigDict(from_attributes=True)


# Audit log schemas
class AuditLogBase(BaseModel):
    user_id: int | None = Field(None)
    action: str = Field(..., max_length=50)  # create, update, delete, confirm, etc.
    module: str = Field(..., max_length=50)  # customers, suppliers, production, etc.
    record_id: int | None = Field(None)
    record_type: str | None = Field(None, max_length=50)
    old_values: str | None = Field(None)  # JSON string
    new_values: str | None = Field(None)  # JSON string
    timestamp: datetime
    ip_address: str | None = Field(None, max_length=50)
    notes: str | None = Field(None)


class AuditLogSchema(AuditLogBase):
    id: int
    user_username: str
    # Human reference for record_type/record_id ("CH-2026-0042", "Shashiji
    # Traders"). Resolved by the /audit-logs handler; None when the row has no
    # id or the record is gone.
    record_label: str | None = Field(None)

    model_config = ConfigDict(from_attributes=True)


# Dashboard schemas
class DashboardStatsSchema(BaseModel):
    raw_material_stock_kg: Decimal
    low_stock_alerts: int
    finished_product_stock_kg: Decimal
    total_customers: int
    total_suppliers: int
    pending_purchases: int
    active_production: int
    pending_challans: int


class DashboardPreferenceBase(BaseModel):
    page_key: str
    config_json: dict


class DashboardPreferenceCreate(DashboardPreferenceBase):
    pass


class DashboardPreferenceSchema(DashboardPreferenceBase):
    id: int
    user_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Backup schemas
class BackupRecordBase(BaseModel):
    filename: str
    file_path: str
    file_size: int
    backup_date: datetime
    description: str | None = None
    is_auto: bool = True


class BackupRecordCreate(BackupRecordBase):
    pass


class BackupRecordSchema(BackupRecordBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("backup_date", "created_at")
    def _ser_ist(self, v: datetime) -> str:
        # Backup timestamps are IST wall time (see _ist_wall in backend).
        # The column holds no offset, so stamp it here: without this the
        # browser guesses the zone and the history can show a shifted time.
        if v is None:
            return v
        text = v.isoformat()
        return text if text.endswith("+05:30") else text + "+05:30"


class SavedReportBase(BaseModel):
    name: str
    report_type: str
    config_json: dict


class SavedReportCreate(SavedReportBase):
    pass


class SavedReportSchema(SavedReportBase):
    id: int
    created_by: int | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


__all__ = [
    "AttachmentBase",
    "AttachmentCreate",
    "AttachmentSchema",
    "AttendanceBase",
    "AttendanceCreate",
    "AttendanceSchema",
    "AuditLogBase",
    "AuditLogSchema",
    "BackupRecordBase",
    "BackupRecordCreate",
    "BackupRecordSchema",
    "ChallanStatus",
    "CustomerBase",
    "CustomerCreate",
    "CustomerPaymentBase",
    "CustomerPaymentCreate",
    "CustomerPaymentSchema",
    "CustomerSchema",
    "CustomerUpdate",
    "DashboardPreferenceBase",
    "DashboardPreferenceCreate",
    "DashboardPreferenceSchema",
    "DashboardStatsSchema",
    "DeductionBase",
    "DeductionCreate",
    "DeductionSchema",
    "EmployeeAdvanceBase",
    "EmployeeAdvanceCreate",
    "EmployeeAdvanceSchema",
    "EmployeeBase",
    "EmployeeCreate",
    "EmployeeSchema",
    "EmployeeStatus",
    "EmployeeType",
    "EmployeeUpdate",
    "ExpenseBase",
    "ExpenseCategorySchema",
    "ExpenseCategoryType",
    "ExpenseCreate",
    "ExpenseSchema",
    "FinishedProductMovementType",
    "FinishedProductStockMovementBase",
    "FinishedProductStockMovementCreate",
    "FinishedProductStockMovementSchema",
    "LeaveBase",
    "LeaveCreate",
    "LeaveSchema",
    "NotificationLogBase",
    "NotificationLogSchema",
    "NotificationRuleBase",
    "NotificationRuleCreate",
    "NotificationRuleSchema",
    "NotificationType",
    "PackagingSizeBase",
    "PackagingSizeCreate",
    "PackagingSizeSchema",
    "PackagingSizeUpdate",
    "PaymentStatus",
    "PaymentStatusSchema",
    "ProductBase",
    "ProductCategoryCreate",
    "ProductCategorySchema",
    "ProductCreate",
    "ProductSchema",
    "ProductUpdate",
    "SizeRateHistorySchema",
    "ProductionEntryBase",
    "ProductionEntryCreate",
    "ProductionEntrySchema",
    "ProductionEntryUpdate",
    "ProductionOutputSchema",
    "ProductionStage",
    "ProductionStageBase",
    "ProductionStageCreate",
    "ProductionStageSchema",
    "ProductionStageUpdate",
    "ProductionStatus",
    "ProductionWastageBase",
    "ProductionWastageCreate",
    "ProductionWastageSchema",
    "PurchaseInvoiceBase",
    "PurchaseInvoiceCreate",
    "PurchaseInvoiceItemBase",
    "PurchaseInvoiceItemCreate",
    "PurchaseInvoiceItemSchema",
    "PurchaseInvoiceSchema",
    "RawMaterialBase",
    "RawMaterialCategoryBase",
    "RawMaterialCategoryCreate",
    "RawMaterialCategoryRawMaterialCategory",
    "RawMaterialCreate",
    "RawMaterialSchema",
    "RawMaterialUpdate",
    "SalaryPaymentBase",
    "SalaryPaymentCreate",
    "SalaryPaymentSchema",
    "SalesChallanBase",
    "SalesChallanCreate",
    "SalesChallanItemBase",
    "SalesChallanItemCreate",
    "SalesChallanItemSchema",
    "SalesChallanSchema",
    "SavedReportBase",
    "SavedReportCreate",
    "SavedReportSchema",
    "SupplierBase",
    "SupplierCreate",
    "SupplierPaymentBase",
    "SupplierPaymentCreate",
    "SupplierPaymentSchema",
    "SupplierSchema",
    "SupplierUpdate",
    "TokenSchema",
    "UnitType",
    "UserBase",
    "UserCreate",
    "UserRole",
    "UserSchema",
    "UserUpdate",
]
