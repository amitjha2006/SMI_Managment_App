from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
import shutil

from sqlalchemy import Numeric, create_engine, event
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.app.core.config import settings

# India is the application's default timezone, for everything. Stored
# values stay naive: SQLite drops timezone offsets on write, so an aware
# value would silently become wall time anyway. The rule is that naive
# means IST wall time - written by _ist_wall below, never by the machine
# clock (datetime.now) or UTC (datetime.utcnow, func.now). Mixing those
# in one table is how session scoping and ageing drifted by hours.
IST = timezone(timedelta(hours=5, minutes=30))


def _ist_now() -> datetime:
    """Current time with an explicit IST offset."""
    return datetime.now(IST)


def _ist_wall() -> datetime:
    """Current IST wall time, naive for storage.

    Use this everywhere a timestamp is written or compared. What lands in
    the column (and what day-boundary comparisons read back) is wall time,
    so stamping IST explicitly keeps stored, compared and displayed times
    in one zone no matter what the machine clock is set to.
    """
    return _ist_now().replace(tzinfo=None)

engine = create_engine(
    settings.DATABASE_URL,
    # The desktop window fires several API requests at once on startup, so
    # reads run on threads. StaticPool would hand every thread the SAME
    # connection and concurrent cursors corrupt each other's results
    # (IndexError/ValueError deep in SQLAlchemy's result processors - only
    # reproducible under load, never in single-threaded tests). The default
    # QueuePool gives each checkout its own connection instead; pooled
    # connections still migrate across threads over their lifetime, which is
    # what check_same_thread=False allows.
    connect_args={"check_same_thread": False},
    echo=settings.DEBUG,
)


# SQLite ships with foreign key enforcement OFF, and it must be switched on per
# connection - a database-wide setting would be nicer but SQLite has no such
# thing. Without this, 58 declared foreign keys are decorative: the schema
# describes relationships nothing checks, and a row can point at a parent that
# was deleted.
#
# Why it is safe to enable now: every constraint was audited against the real
# database first and found 0 violating rows, so no existing operation can start
# failing because of data that is already there. See tests/test_foreign_keys.py,
# which re-runs that audit so the claim cannot rot.
#
# The consequence to be aware of: deleting a parent row that children still
# reference now raises IntegrityError instead of quietly leaving orphans. That is
# the intended behaviour - but it means a delete path that relied on leaving
# debris behind will now error, which is the correct time to find out.
@event.listens_for(engine, "connect")
def _enforce_foreign_keys(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
    finally:
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


# Money is stored at exactly 2 decimals, not just displayed that way.
# SQLite ignores Numeric(15,2) scale, and a 3-decimal Decimal can come back
# shifted by binary-float artefacts (5.555 read back as 5.55), so the flush
# rounds every scale-2 money/rate column with ROUND_HALF_UP on the way in.
# Quantity columns (scale 3, kilograms) are deliberately untouched, as are
# ints (already integral) and NULLs. One session-level hook covers every
# model with no per-endpoint edits; pinned by backend/tests/test_money_scale.py.
_MONEY_QUANT = Decimal("0.01")
_MONEY_SCALE = 2


def _round_money_columns(target):
    mapper = type(target).__mapper__
    for prop in mapper.column_attrs:
        column = prop.columns[0]
        if not isinstance(column.type, Numeric) or (column.type.scale or 0) != _MONEY_SCALE:
            continue
        value = getattr(target, prop.key, None)
        if isinstance(value, float):
            value = Decimal(str(value))
        if isinstance(value, Decimal):
            setattr(target, prop.key, value.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP))


def _round_money_in_session(session, _flush_context, _instances):
    for obj in list(session.new):
        _round_money_columns(obj)
    for obj in list(session.dirty):
        _round_money_columns(obj)


event.listen(Session, "before_flush", _round_money_in_session)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    # Import all models here to register them with Base.metadata
    from backend.app.models import (  # noqa: F401,PLC0415 - models import Base here
        attachment,
        backup,
        customer,
        employee,
        expense,
        finished_product,
        notification,
        print_footer,
        product,
        production,
        purchase,
        raw_material,
        sales,
        user,
    )

    Base.metadata.create_all(bind=engine)
    _ensure_columns()
    _migrate_product_categories()
    _seed_expense_categories()


def _migrate_product_categories():
    """One-time: move products.category (free text) into product_categories.

    Creates a category row per distinct existing value and repoints every
    product, then drops the old column. Idempotent: once products.category is
    gone there is nothing to move and the function returns immediately.
    """
    with engine.begin() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(products)").fetchall()}
        if "category" not in cols:
            return  # already migrated
        legacy = [
            r[0]
            for r in conn.exec_driver_sql(
                "select distinct category from products "
                "where category is not null and trim(category) <> ''"
            )
        ]
        for raw in legacy:
            name = (raw or "").strip()
            if not name:
                continue
            row = conn.exec_driver_sql(
                "select id from product_categories where lower(name)=lower(?)", (name,)
            ).fetchone()
            if row:
                cid = row[0]
            else:
                cid = conn.exec_driver_sql(
                    "insert into product_categories (name, is_active) values (?,1)", (name,)
                ).lastrowid
            conn.exec_driver_sql(
                "update products set category_id=? where trim(category)=?", (cid, name)
            )
        # Anything left uncategorised stays NULL; drop the text column so there
        # is a single source of truth.
        try:
            conn.exec_driver_sql("alter table products drop column category")
            print("[products] category -> category_id (legacy column dropped)")
        except Exception as e:  # pragma: no cover - older SQLite
            print(f"[products] could not drop legacy category column: {e}")


def _ensure_columns():
    """Idempotent lightweight migrations: ADD COLUMNs that create_all skips
    on existing tables. Each entry is safe to re-run."""
    pending = [
        ("sales_challans", "dispatched_at", "DATETIME"),
        ("sales_challans", "delivered_at", "DATETIME"),
        ("sales_challans", "transporter", "VARCHAR(100)"),
        ("sales_challans", "vehicle_no", "VARCHAR(50)"),
        # Products moved from a free-text category to a real category master.
        ("products", "category_id", "INTEGER"),
    ]
    # Printed challans must stay historically true. Company details, the
    # customer's printed details and each line's product/size names are frozen
    # onto the challan at creation, so editing a master record later cannot
    # rewrite what an already-issued document says. All nullable: existing rows
    # stay NULL until the company profile is first saved.
    # company_snapshot_at is the "has this been stamped?" marker - company_name
    # alone cannot tell us, because a deliberately blank name is legitimate.
    for _col in (
        "company_name",
        "company_address",
        "company_gstin",
        "company_phone",
        "company_email",
        "company_terms",
        "cust_company_name",
        "cust_address",
        "cust_gst_no",
        "cust_contact_number",
        "company_snapshot_at",
    ):
        pending.append(
            ("sales_challans", _col, "TEXT" if _col != "company_snapshot_at" else "DATETIME")
        )
    pending.append(("sales_challan_items", "product_name", "TEXT"))
    pending.append(("sales_challan_items", "size_name", "TEXT"))
    # Which print footer was picked for a challan. Plain INTEGER, not a real
    # FK: deleting a footer must stay safe, and the frozen company_terms text
    # is the print source of truth, not this pointer.
    pending.append(("sales_challans", "footer_id", "INTEGER"))
    # Spec 3/15: soft delete / archive for financial + inventory transactions.
    archived_tables = [
        "sales_challans",
        "customer_payments",
        "purchase_invoices",
        "supplier_payments",
        "production_entries",
        "expenses",
    ]
    for table in archived_tables:
        pending.append((table, "is_archived", "BOOLEAN NOT NULL DEFAULT 0"))
        pending.append((table, "archived_at", "DATETIME"))
        pending.append((table, "archived_by", "INTEGER"))
    # GST rate per product, feeding the sales invoice (a tax document that
    # extracts GST from GST-inclusive challan amounts). NOT NULL DEFAULT 0 is
    # safe on existing rows: 0 means "not configured", and invoice creation
    # refuses to guess a rate, so no product silently invoices at the wrong one.
    pending.append(("products", "gst_rate", "NUMERIC(5,2) NOT NULL DEFAULT 0"))
    # Per-product free-text help (Product Help window). Nullable plain TEXT:
    # existing rows stay NULL, which simply means "no help written".
    pending.append(("products", "help_text", "TEXT"))
    # HSN/SAC code for the sales tax invoice. Nullable and unconstrained: the
    # codes are the business's to supply, and inventing or defaulting one would
    # be worse than leaving it blank for them to fill in.
    pending.append(("products", "hsn_code", "VARCHAR(12)"))
    # HSN picker for raw materials (mirrors products.hsn_code).
    pending.append(("raw_materials", "hsn_code", "VARCHAR(12)"))
    # HSNCode timestamps (created_at, updated_at) - for universal timestamp rule
    pending.append(("hsn_codes", "created_at", "DATETIME"))
    pending.append(("hsn_codes", "updated_at", "DATETIME"))
    # BackupRecord created_by - who created the backup
    pending.append(("backup_records", "created_by", "INTEGER"))
    # Watch toggle for low-stock alerts. NOT NULL DEFAULT 1 is safe on
    # existing rows: everything stays watched, so no alert silently stops.
    pending.append(("raw_materials", "watch_stock", "BOOLEAN NOT NULL DEFAULT 1"))
    # Per-size finished-goods threshold (packets). NOT NULL DEFAULT 0 is safe
    # on existing rows: 0 only alerts when the size is fully out, so no
    # existing stock suddenly flags as low.
    pending.append(("packaging_sizes", "threshold_packets", "NUMERIC(15,3) NOT NULL DEFAULT 0"))
    # Empty-pouch weight per pack size (kg). Same safety: existing rows read
    # 0, which simply disables wrapper accounting until set.
    pending.append(("packaging_sizes", "wrapper_weight_kg", "NUMERIC(10,5) NOT NULL DEFAULT 0"))
    # Which raw material (film roll) pouches this size. Nullable on purpose:
    # existing rows read NULL, which the production form treats as "not
    # mapped yet" and asks for a roll instead of guessing one.
    pending.append(("packaging_sizes", "wrapper_material_id", "INTEGER"))
    # Packing gain on packaging stages (output above input from wrapper
    # weight). Same safety as wrapper_weight_kg: existing rows read 0, and
    # the backfill script derives historical gains from stored input/output.
    pending.append(
        ("production_stage_entries", "packing_gain_kg", "NUMERIC(15,3) NOT NULL DEFAULT 0")
    )
    # Purchase invoices: transport (freight) charges the merchant paid on top —
    # informational for landed-cost per kg, never part of the payable total.
    # NOT NULL DEFAULT 0 keeps every existing invoice untouched.
    pending.append(("purchase_invoices", "transport_charges", "NUMERIC(15,2) NOT NULL DEFAULT 0"))
    pending.append(
        ("purchase_invoice_items", "transport_amount", "NUMERIC(15,2) NOT NULL DEFAULT 0")
    )
    pending.append(("purchase_invoice_items", "other_amount", "NUMERIC(15,2) NOT NULL DEFAULT 0"))
    # Customer credit limit (₹). Same safety: 0 means unlimited, so no
    # existing customer suddenly blocks confirms.
    pending.append(("customers", "credit_limit", "NUMERIC(15,2) NOT NULL DEFAULT 0"))
    # Dashboard theme ("light"/"dark"). Nullable: old rows read NULL and
    # the endpoints treat that as light, so no backfill can mis-theme anyone.
    pending.append(("dashboard_preferences", "theme", "VARCHAR(10)"))
    # Payroll periods. salary_payments had no period at all, which made
    # "which month is this for?" unanswerable and a duplicate run easy.
    pending.append(("salary_payments", "period_month", "VARCHAR(7)"))
    pending.append(("salary_payments", "advance_recovered", "NUMERIC(15,2) NOT NULL DEFAULT 0"))
    pending.append(("salary_payments", "payment_mode", "VARCHAR(30)"))
    pending.append(("salary_payments", "is_archived", "BOOLEAN NOT NULL DEFAULT 0"))
    pending.append(("salary_payments", "archived_at", "DATETIME"))
    pending.append(("salary_payments", "archived_by", "INTEGER"))
    pending.append(("deductions", "period_month", "VARCHAR(7)"))
    pending.append(("deductions", "is_applied", "BOOLEAN NOT NULL DEFAULT 0"))
    pending.append(("deductions", "applied_in", "VARCHAR(7)"))
    pending.append(("deductions", "created_by", "INTEGER"))
    _ensure_production_pack_columns()
    # Columns first...
    with engine.begin() as conn:
        for table, column, ddl in pending:
            existing = {
                row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
            }
            if column not in existing:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        _seed_permissions()


def _ensure_production_pack_columns():
    """Add the two-step production columns that create_all skips on old DBs.

    packed_at on production_entries, movement_date on both stock-movement
    tables. Idempotent: each ADD COLUMN is skipped when the column exists.
    """
    pending = [
        ("production_entries", "packed_at", "DATETIME"),
        ("finished_product_stock_movements", "movement_date", "DATETIME"),
        ("raw_material_stock_movements", "movement_date", "DATETIME"),
    ]
    with engine.begin() as conn:
        for table, column, ddl in pending:
            existing = {
                row[1] for row in conn.exec_driver_sql(
                    f"PRAGMA table_info({table})"
                ).fetchall()
            }
            if column not in existing:
                conn.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"
                )
        # A batch ground before it is packed has no pack size yet, so this
        # column has to be nullable. SQLite cannot drop NOT NULL in place, so
        # that one migration rebuilds the table via a script. Say so plainly
        # instead of failing at pack time with a bare IntegrityError.
        info = conn.exec_driver_sql(
            "PRAGMA table_info(production_entries)"
        ).fetchall()
        if any(row[1] == "packaging_size_id" and row[3] for row in info):
            print(
                "[production] packaging_size_id is still NOT NULL - run "
                "scripts/make_pack_size_optional.py to allow unpacked batches",
                flush=True,
            )
    # ...then the indexes that depend on them, in a second transaction. Running
    # these first would fail on a database that predates the columns, because
    # SQLite cannot create an index over a column that does not exist yet.
    with engine.begin() as conn:
        # ALTER TABLE cannot add a UNIQUE constraint, so the payroll guards are
        # built as unique indexes here. They are what make paying a month twice
        # impossible.
        for table, columns, name in [
            ("salary_payments", "employee_id, period_month", "uq_salary_payment_period"),
            ("attendance", "employee_id, date", "uq_attendance_employee_date"),
        ]:
            existing = {
                r[0]
                for r in conn.exec_driver_sql(
                    "select name from sqlite_master where type='index'"
                ).fetchall()
            }
            if name in existing:
                continue
            # A NULL period_month would let duplicates through, since NULL !=
            # NULL in a unique index. Those are pre-existing rows from before
            # periods existed; give each a distinct value so the index can be
            # created and any future duplicate is still caught. 'legacy-<id>'
            # also keeps them visibly distinct from a real YYYY-MM period.
            if table == "salary_payments":
                conn.exec_driver_sql(
                    "update salary_payments set period_month = "
                    "'legacy-' || id where period_month is null"
                )
            conn.exec_driver_sql(f"create unique index {name} on {table} ({columns})")
        _ensure_pack_size_index(conn)
        _ensure_date_indexes(conn)


def _ensure_pack_size_index(conn) -> None:
    """One LIVE row per (product, size name) - the rule that stops duplicates.

    Two concurrent "add 100g" requests both read the size list before either
    committed and both inserted (the Cumin 100g duplicate, 19 ms apart), so the
    rule has to live in the database, not only in the request handler. Partial
    on is_active: an inactive duplicate is ordinary history (deactivated twins
    from an old merge), and the app restores that row when the name comes back.
    """
    if "uq_pack_size_per_product" in {
        r[0] for r in conn.exec_driver_sql("select name from sqlite_master where type='index'")
    }:
        return
    try:
        conn.exec_driver_sql(
            "create unique index uq_pack_size_per_product "
            "on packaging_sizes (product_id, size_name) where is_active = 1"
        )
    except (IntegrityError, OperationalError) as exc:
        # A database that predates this guard may already hold a duplicate.
        # Never refuse to start over it: name the rows and let the app run, so
        # the repair can happen with the app open.
        dupes = conn.exec_driver_sql(
            "select product_id, size_name, count(*) from packaging_sizes "
            "group by product_id, size_name having count(*) > 1"
        ).fetchall()
        print(
            f"[pack sizes] unique index not created ({exc}); "
            f"duplicate rows: {[tuple(r[:2]) for r in dupes]}",
            flush=True,
        )


# Date-ordered lists sort by business date, so each carries an index on its
# date column (declared index=True on the model for fresh databases,
# backfilled here for existing ones). Without these, every list open sorts
# the whole table - invisible at 1k rows, painful at 50k.
_DATE_INDEXES = [
    ("sales_challans", "challan_date"),
    ("sales_invoices", "invoice_date"),
    ("purchase_invoices", "invoice_date"),
    ("expenses", "date"),
    ("production_entries", "production_date"),
    ("customer_payments", "payment_date"),
    ("supplier_payments", "payment_date"),
    ("audit_logs", "timestamp"),
]


def _ensure_date_indexes(conn) -> None:
    """CREATE INDEX IF NOT EXISTS for every date-ordered list. Idempotent."""
    for table, column in _DATE_INDEXES:
        cols = {
            row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in cols:
            continue
        conn.exec_driver_sql(
            f"CREATE INDEX IF NOT EXISTS ix_{table}_{column} ON {table} ({column})"
        )


def _seed_permissions():
    """Populate the permissions catalogue (spec 3).

    Adds any permission that is missing rather than only seeding an empty
    table, so a permission added to the catalogue later shows up in the admin
    Permissions screen on an existing database.
    """
    from backend.app.core.permissions import PERMISSIONS  # noqa: PLC0415 - cycle via models
    from backend.app.models.user import Permission  # noqa: PLC0415 - models import Base here

    db = SessionLocal()
    try:
        existing = {row[0] for row in db.query(Permission.name).all()}
        added = 0
        for name, module, action, description, _defaults in PERMISSIONS:
            if name in existing:
                continue
            db.add(Permission(name=name, module=module, action=action, description=description))
            added += 1
        if added:
            db.commit()
            print(f"[permissions] seeded {added} new permission(s)")
    except Exception:
        db.rollback()
    finally:
        db.close()


# Standard expense categories for a masala factory. Seeded additively so a
# fresh or cleaned database offers the real dropdown from day one; rows a
# business added itself (or deactivated) are never touched.
STANDARD_EXPENSE_CATEGORIES = (
    "Electricity",
    "Factory Rent",
    "Fuel",
    "Loading & Unloading",
    "Machinery Repair & Maintenance",
    "Miscellaneous",
    "Packaging Material",
    "Printing & Stationery",
    "Salaries & Wages",
    "Staff Food",
    "Taxes & Compliance",
    "Telephone & Internet",
    "Transport & Freight",
    "Water",
)


def _seed_expense_categories(db=None):
    """Insert any missing standard expense categories. Returns the count.

    Takes an explicit session in tests; otherwise opens its own on the
    configured engine (same pattern as _seed_permissions).
    """
    from backend.app.models.expense import ExpenseCategory  # noqa: PLC0415 - cycle via models

    own = db is None
    if own:
        db = SessionLocal()
    added = 0
    try:
        existing = {row[0] for row in db.query(ExpenseCategory.name).all()}
        for name in STANDARD_EXPENSE_CATEGORIES:
            if name in existing:
                continue
            db.add(ExpenseCategory(name=name, is_active=True))
            added += 1
        if added:
            db.commit()
            print(f"[expenses] seeded {added} new categor(ies)")
    except Exception:
        db.rollback()
        raise
    finally:
        if own:
            db.close()
    return added


def _live_db_path():
    """Path to the live (writable) SQLite database file."""
    from pathlib import Path
    return Path(settings.DB_PATH)


def _do_backup(db: Session, is_auto: bool = True, description: str = "", prefix: str = ""):
    """Copy the live DB to the backup folder and record it.

    Returns the BackupRecord row. The backup folder comes from the
    ``backup.folder`` application setting when set, otherwise
    ``data/backups/`` next to the live DB.
    """
    from pathlib import Path
    from backend.app.models import BackupRecord, ApplicationSetting

    live = _live_db_path()

    folder_row = db.query(ApplicationSetting).filter(ApplicationSetting.key == "backup.folder").first()
    backup_folder = Path(folder_row.value) if (folder_row and folder_row.value) else live.parent / "backups"
    backup_folder.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{prefix}{ts}.db" if prefix else f"backup_{ts}.db"
    dest = backup_folder / filename
    shutil.copy2(str(live), str(dest))

    rec = BackupRecord(
        filename=filename, file_path=str(dest),
        file_size=dest.stat().st_size,
        backup_date=_ist_wall(),
        description=description, is_auto=is_auto,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec
