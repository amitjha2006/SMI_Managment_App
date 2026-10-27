"""
Shresth Masala Industry Management System — FastAPI Application

This is the main entry point. All HTTP routes are organized in backend/app/routes/
and included here in dependency order (static paths before parameterized ones).
"""
import io
import json
import json as _json
import random
import re
import re as _re
import shutil
import smtplib
import sqlite3
import sys
import threading
import time
import uuid
import zipfile
from collections import Counter, defaultdict
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from decimal import Decimal as _D
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from pathlib import Path as _Path

from fastapi import Body, Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, object_session

from backend.app.core.config import settings
from backend.app.core.database import (
    IST,
    SessionLocal,
    _ist_now,
    _ist_wall,
    engine,
    get_db,
    init_db,
)
from backend.app.core.security import (
    create_access_token,
    decode_token,
    get_password_hash,
    verify_password,
)
from backend.app.models import (
    ApplicationSetting,
    Attendance,
    AuditLog,
    BackupRecord,
    CashMovement,
    Customer,
    CustomerPayment,
    DashboardPreference,
    DashboardShare,
    Deduction,
    Employee,
    EmployeeAdvance,
    Expense,
    ExpenseCategory,
    FinishedProductStockMovement,
    HSNCode,
    ImportReport,
    Leave,
    NotificationLog,
    PackagingSize,
    PrintFooter,
    Product,
    ProductCategory,
    ProductFormulation,
    ProductFormulationStep,
    ProductionEntry,
    ProductionOutput,
    ProductionStageEntry,
    PurchaseInvoice,
    PurchaseInvoiceItem,
    RawMaterial,
    RawMaterialCategory,
    RawMaterialStockMovement,
    SalaryPayment,
    SalesChallan,
    SalesChallanItem,
    SalesInvoice,
    SalesInvoiceItem,
    SavedReport,
    SizeRateHistory,
    Supplier,
    SupplierPayment,
    User,
)
from backend.app.models.user import UserRole
from backend.app.schemas import (
    AuditLogSchema,
    BackupRecordSchema,
    ChallanStatus,
    CustomerCreate,
    CustomerPaymentSchema,
    CustomerSchema,
    CustomerUpdate,
    EmployeeSchema,
    EmployeeStatus,
    EmployeeType,
    ExpenseCategorySchema,
    ExpenseSchema,
    FinishedProductMovementType,
    PackagingSizeSchema,
    PaymentStatus,
    PrintFooterCreate,
    PrintFooterSchema,
    ProductCategoryCreate,
    ProductCategorySchema,
    ProductionEntrySchema,
    ProductionStage,
    ProductionStatus,
    ProductSchema,
    PurchaseInvoiceSchema,
    RawMaterialCategoryCreate,
    RawMaterialCategoryRawMaterialCategory,
    RawMaterialCreate,
    RawMaterialSchema,
    RawMaterialUpdate,
    SalesChallanSchema,
    SizeRateHistorySchema,
    SupplierCreate,
    SupplierPaymentSchema,
    SupplierSchema,
    SupplierUpdate,
    TokenSchema,
    UserCreate,
    UserSchema,
    UserUpdate,
)
from backend.app.services import cash_balance as _cash
from backend.app.services import challan_snapshot as _snap
from backend.app.services import document_sequence as _seq
from backend.app.services import payroll as _pay
from backend.app.services import production_workflow as _wf
from backend.app.services import sales_invoice as _inv
from backend.app.services.amount_in_words import amount_in_words
from backend.app.services.challan_snapshot import company_profile, stamp_unstamped
from backend.app.services.excel_import import IMPORTERS, read_excel_file, run_import
from backend.app.services.production_wastage import aggregate_by_category, stage_loss
from backend.app.services.stock_posting import challan_stock_posted, post_challan_sales_movements

# Import route modules
from backend.app.routes import (
    auth,
    backup,
    customers,
    dashboard,
    employees,
    import_export,
    production,
    products,
    purchase,
    raw_materials,
    reports,
    sales,
    suppliers,
    users,
)
from backend.app.routes import settings as settings_routes

# Import shared utilities (also used by route modules)
from backend.app.core.shared import (
    _audit,
    _archive_record,
    _change_reason,
    _challan_entry_allowed,
    _current_user,
    _editor_mode_always_allowed,
    _FULL_PERCENT_SHARE,
    _get_setting,
    _iso_ist,
    _low_finished_sizes,
    _MAX_AUDIT_KEPT,
    _MAX_LOCK_MINUTES,
    _MIN_PASSWORD_LEN,
    _prune_audit_log,
    _reject_if_archived,
    _request_payload,
    _request_user_and_role,
    _restore_record,
    _require_admin,
    _send_email_test,
    _send_login_alert,
    _send_scheduled_report,
    _set_setting,
    _unknown_import_type,
    _EDITOR_MODE_EXEMPT,
    _EDITOR_MODE_EXEMPT_PREFIX,
    _ist_wall,
)
from backend.app.core.permissions import ADMIN_ONLY_PREFIXES

# Initialize database
init_db()


@asynccontextmanager
async def _lifespan(_app: "FastAPI"):
    """Start background jobs when the app begins serving, not when imported.

    The auto-backup scheduler used to be started by a bare module-level call.
    That looked harmless, but it meant ANY process importing this module -
    including the test suite - spawned a thread which, once the wall clock
    passed the configured backup hour, copied the real database and inserted
    a real backup row. Two consequences, both observed:

      * The test suite's "no test may touch the live database" tripwire failed
        whenever the suite was the first process of the day past that hour.
      * A plain test run quietly created backup files in data/backups.

    A lifespan handler fires only when something actually serves the app.
    Both entry points run uvicorn against `app` (main_window.py and the
    __main__ block below), so the packaged app and `python backend/main.py`
    both still get the scheduler.
    """
    start_auto_backup_scheduler()
    try:
        yield
    finally:
        # The engine is built on a StaticPool, which holds one sqlite3
        # connection open for the lifetime of the process. Without an explicit
        # dispose, that connection is only reclaimed by the garbage collector
        # during interpreter shutdown, which Python reports as
        # "ResourceWarning: unclosed database". It was cosmetic - the OS closes
        # the handle either way - but it made a clean exit look like a fault and
        # would hide a genuine leak in future. Verified by
        # tests/test_clean_shutdown.py.
        engine.dispose()


app = FastAPI(
    title=settings.APP_NAME,
    description="Shresth Masala Industry Management System",
    version="0.1.0",
    lifespan=_lifespan,
)

# Serve frontend static files (bundled under sys._MEIPASS when frozen)


def _frontend_dir() -> str:

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cand = str(Path(meipass) / "frontend")
        if Path(cand).is_dir():
            return cand
    return str(settings.FRONTEND_DIR)


FRONTEND_DIR = _frontend_dir()
app.mount("/frontend", StaticFiles(directory=FRONTEND_DIR), name="frontend")


# Override root to serve dashboard
@app.get("/")
def root():

    return FileResponse(str(Path(FRONTEND_DIR) / "index.html"))


@app.get("/app/env")
def app_env():
    import os
    return {"env": os.environ.get("APP_ENV", "production").lower()}


@app.middleware("http")
async def no_cache_frontend(request, call_next):
    # Frontend files change often during development; never let the
    # browser serve stale HTML/JS for the app shell and pages.
    response = await call_next(request)
    if (
        request.url.path == "/"
        or request.url.path.startswith("/frontend")
        or request.url.path.startswith("/share")
    ):
        response.headers["Cache-Control"] = "no-store"
    return response


# Include route modules in dependency order (static paths before parameterized)
# The order matters for route matching!
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(dashboard.router)
app.include_router(customers.router)
app.include_router(suppliers.router)
app.include_router(raw_materials.router)
app.include_router(products.router)
app.include_router(production.router)
app.include_router(purchase.router)
app.include_router(sales.router)
app.include_router(employees.router)
app.include_router(reports.router)
app.include_router(settings_routes.router)
app.include_router(backup.router)
app.include_router(import_export.router)

# Test-facing aliases: the test suite calls route handlers as main.<handler>
# (the pre-routes layout exposed them on main). Keep them importable here.
finished_stock_balance = sales.finished_stock_balance
finished_stock = sales.list_finished_stock
list_finished_stock = sales.list_finished_stock
list_stock_adjustments = sales.list_stock_adjustments
create_stock_adjustment = sales.create_stock_adjustment
create_challan = sales.create_challan
update_challan = sales.update_challan
update_draft_challan = sales.update_draft_challan
update_draft_challan_item = sales.update_draft_challan_item
get_dashboard_stats = dashboard.get_dashboard_stats
create_production_entry = production.create_production_entry
pack_production_entry = production.pack_production_entry
set_production_status = production.set_production_status
get_production_materials = production.get_production_materials
update_production_stages = production.update_production_stages
list_production_entries = production.list_production_entries
archive_production = production.archive_production_entry

# Additional test-facing aliases from other route modules
create_product = products.create_product
update_packaging_size = products.update_packaging_size
list_expenses = reports.list_expenses
list_purchase_invoices = purchase.list_purchase_invoices
get_session_settings = settings_routes.get_session_settings
update_session_settings = settings_routes.update_session_settings
update_email_settings = settings_routes.update_email_settings
create_user = users.create_user
payroll_run = employees.run_payroll

# Sales route aliases
create_sales_invoice = sales.create_sales_invoice
create_customer_payment = sales.create_customer_payment

# Backup test-facing aliases
from backend.app.routes import backup as backup_routes
list_backup_records = backup_routes.list_backup_records
_backup_dir = backup_routes._backup_dir

# Sales challan status alias (test uses set_challan_status)
set_challan_status = sales.update_challan_status

# Products route aliases
get_product_formulation = products.get_product_formulation
update_product_formulation = products.update_product_formulation
get_product_workflow = products.get_product_workflow
update_product_workflow = products.update_product_workflow
get_product_help = products.get_product_help
update_product_help = products.update_product_help
list_hsn_codes = products.list_hsn_codes
suggest_hsn_codes = products.suggest_hsn_codes
list_product_categories = products.list_product_categories
create_product_category = products.create_product_category
update_product_category = products.update_product_category
delete_product_category = products.delete_product_category
list_print_footers = products.list_print_footers
create_print_footer = products.create_print_footer
update_print_footer = products.update_print_footer
delete_print_footer = products.delete_print_footer
packaging_rate_history = products.size_rate_history
size_rate_history = products.size_rate_history
delete_packaging_size = products.delete_packaging_size

# Reports route aliases
sales_trend = reports.sales_trend
raw_material_summary = reports.raw_material_summary
purchase_summary = reports.purchase_summary
payment_summary = reports.payment_summary
production_history = reports.production_history
profit_loss = reports.profit_loss
summary_report = reports.summary_report
recent_activity = reports.recent_activity
custom_report_fields = reports.custom_report_fields
run_custom_report = reports.run_custom_report
export_custom_report = reports.export_custom_report
list_saved_reports = reports.list_saved_reports
create_saved_report = reports.create_saved_report
update_saved_report = reports.update_saved_report
delete_saved_report = reports.delete_saved_report
list_expense_categories = reports.list_expense_categories
expense_history = reports.expense_history
create_expense = reports.create_expense
dues_followup = reports.dues_followup

# Settings route aliases
get_backup_settings = settings_routes.get_backup_settings
get_sales_invoice_settings = settings_routes.get_sales_invoice_settings
get_company_settings = settings_routes.get_company_settings
update_company_settings = settings_routes.update_company_settings
get_email_settings = settings_routes.get_email_settings
test_email = settings_routes.test_email
send_scheduled_report_endpoint = settings_routes.send_scheduled_report_endpoint
preview_email = settings_routes.preview_email

# Users route aliases
list_users = users.list_users
update_user = users.update_user
deactivate_user = users.deactivate_user
set_user_password = users.set_user_password
list_audit_logs = users.list_audit_logs
list_permissions = users.list_permissions
update_permissions = users.update_permissions

# Employees route aliases
list_employees = employees.list_employees
create_employee = employees.create_employee
get_employee = employees.get_employee
update_employee = employees.update_employee
archive_employee = employees.archive_employee
restore_employee = employees.restore_employee
list_attendance = employees.list_attendance
record_attendance = employees.record_attendance
list_leaves = employees.list_leaves
record_leave = employees.record_leave
list_advances = employees.list_advances
record_advance = employees.record_advance
list_deductions = employees.list_deductions
record_deduction = employees.record_deduction
list_payroll = employees.list_payroll
archive_payroll_payment = employees.archive_payroll_payment

# Purchase route aliases
get_purchase_invoice = purchase.get_purchase_invoice
create_purchase_invoice = purchase.create_purchase_invoice
archive_purchase_invoice = purchase.archive_purchase_invoice
restore_purchase_invoice = purchase.restore_purchase_invoice
list_supplier_payments = purchase.list_supplier_payments
create_supplier_payment = purchase.create_supplier_payment
archive_supplier_payment = purchase.archive_supplier_payment
restore_supplier_payment = purchase.restore_supplier_payment

# Production route aliases
get_production_entry = production.get_production_entry
restore_production_entry = production.restore_production_entry

# Dashboard route aliases
production_today = dashboard.production_today
list_dashboard_preferences = dashboard.list_dashboard_preferences
upsert_dashboard_preference = dashboard.upsert_dashboard_preference
recent_notifications = dashboard.recent_notifications
unread_notifications_count = dashboard.unread_notifications_count
client_log = dashboard.client_log
clear_notifications = dashboard.clear_notifications
get_dashboard_share = dashboard.get_dashboard_share
create_dashboard_share = dashboard.create_dashboard_share
list_dashboard_shares = dashboard.list_dashboard_shares
revoke_dashboard_share = dashboard.revoke_dashboard_share

# Customers route aliases (if needed)
# Suppliers route aliases (if needed)
# Raw materials route aliases (if needed)


# CORS - the packaged app is same-origin; this only covers a separate dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# RBAC middleware - must be added AFTER routes are registered
@app.middleware("http")
async def rbac_guard(request, call_next):

    path = request.url.path
    # Public: login, docs, app shell + static files, preflight
    if (
        request.method == "OPTIONS"
        or path == "/"
        or path.startswith("/frontend")
        or path.startswith("/share")
        or (path.startswith("/dashboard/share/") and request.method == "GET")
        or (path == "/notifications/client-log" and request.method == "POST")
        or path in ("/docs", "/openapi.json", "/redoc", "/favicon.ico")
        or path == "/auth/login"
        or path == "/app/env"
    ):
        return await call_next(request)
    # First-user bootstrap: open registration only when no users exist yet
    if path == "/auth/register" and request.method == "POST":
        db = SessionLocal()
        try:
            if db.query(User).count() == 0:
                return await call_next(request)
        finally:
            db.close()
    db = SessionLocal()
    try:
        user, role = _request_user_and_role(db, request)
    finally:
        db.close()
    if not user:
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})

    # Fixed role floors - these are the role definitions, not configurable.
    if role == "viewer" and request.method != "GET":
        return JSONResponse(status_code=403, content={"detail": "Viewers have read-only access"})
    if role == "data_entry" and request.method == "DELETE":
        return JSONResponse(status_code=403, content={"detail": "Data entry users cannot delete"})
    if role == "challan_entry" and not _challan_entry_allowed(request.method, path):
        return JSONResponse(
            status_code=403,
            content={"detail": "Challan Entry profiles can only view and create challans"},
        )

    # Spec 3: Admin may configure role permissions. The checks below are the
    # built-in defaults; an explicit Admin choice for this role overrides them.
    from backend.app.core import permissions as _perm

    db2 = SessionLocal()
    try:
        allowed = lambda name: _perm.resolve(db2, role, name)  # noqa: E731

        # Admin-only prefixes from centralized config
        admin_only = False
        for prefix in ADMIN_ONLY_PREFIXES:
            if path.startswith(prefix):
                admin_only = True
                break
        # Explicit paths
        if path in ("/auth/register", "/permissions"):
            admin_only = True

        # Reading company profile is not admin action; writing it stays admin-only
        if path in ("/settings/company", "/settings/session") and request.method == "GET":
            admin_only = False
        if admin_only and role != "admin":
            return JSONResponse(status_code=403, content={"detail": "Admin access required"})

        if role not in ("admin", "manager"):
            if path.endswith("/status"):
                if not (allowed("sales.confirm") or allowed("production.approve")):
                    return JSONResponse(
                        status_code=403, content={"detail": "Manager access required"}
                    )
            elif path == "/audit-logs":
                if not allowed("audit.view"):
                    return JSONResponse(
                        status_code=403, content={"detail": "Manager access required"}
                    )
            elif (path.startswith("/import") and not allowed("import.run")) or (
                path.endswith("/restore") and not allowed("records.restore")
            ):
                return JSONResponse(status_code=403, content={"detail": "Manager access required"})

        if request.method != "GET" and role not in ("admin", "manager"):
            perm = None
            if path in ("/supplier-payments",) or path.startswith("/supplier-payments/"):
                perm = "purchases.pay"
            elif path in ("/payments",) or path.startswith("/payments/"):
                perm = "sales.collect"
            elif path in ("/stock-adjustments", "/finished-stock/opening"):
                perm = "inventory.adjust"
            elif path.endswith("/dispatch") or path.endswith("/deliver"):
                perm = "sales.dispatch"
            elif path.startswith("/packaging-sizes"):
                # changing a per-size price is master-data editing
                perm = "masters.manage"
            elif "/cash" in path:
                # Recording or voiding a cash-float movement is money moving
                # with a party, so it is gated by the matching PAYMENT
                # permission, not masters.manage. masters.manage is one data
                # entry legitimately holds in order to create customers and
                # products, so using it here would let the lowest role silently
                # adjust what the factory is owed.
                perm = "sales.collect" if path.startswith("/customers") else "purchases.pay"
            elif path.startswith("/payroll"):
                # Running payroll pays real money to staff, and so does
                # recording an advance or a deduction. Deliberately NOT
                # employees.manage, which data entry holds for the employee
                # master and attendance.
                perm = "payroll.manage"
            elif "/advances" in path or "/deductions" in path:
                perm = "payroll.manage"
            elif path.startswith("/products") and request.method in ("PUT", "PATCH"):
                perm = "masters.manage"
            if perm and not allowed(perm):
                return JSONResponse(
                    status_code=403, content={"detail": f"Permission required: {perm}"}
                )

        # Editor Mode - a second, deliberate gate on top of role permissions.
        # Holding "edit" or "delete" rights is not enough: the signed-in user
        # must also have switched Editor Mode on for this session. Enforced
        # here rather than in the UI, because a hidden button is not a control
        # (any request can be made directly against the API).
        if request.method in ("PUT", "PATCH", "DELETE") and not _editor_mode_always_allowed(path):
            payload = _request_payload(request)
            if not (payload and payload.get("editor")):
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": "Editor Mode is off. Switch Editor Mode on "
                        "to edit or delete records."
                    },
                )
    finally:
        db2.close()
    return await call_next(request)


@app.middleware("http")
async def no_store_for_api(request: Request, call_next):
    """Mark API responses as uncacheable.

    Without this the server sends no Cache-Control at all, which leaves the
    WebView free to reuse a stored response. Auto-refresh then re-requests the
    dashboard on its timer and re-renders the previous numbers, so the figures
    look frozen even though requests are genuinely being made.

    Stated as "everything except static files" rather than an enumeration of
    API prefixes: a prefix list silently omits whatever endpoint was added last,
    which is how the new finished-stock route ended up cacheable while its
    prefix was absent from the list. Static files are already handled by
    no_cache_frontend above, and images/fonts would be the only things worth
    caching, so the small cost of revalidating them is the safer trade.
    """
    response = await call_next(request)
    path = request.url.path
    static = (
        path == "/"
        or path.startswith("/frontend")
        or path.startswith("/share")
        or path.startswith("/assets")
    )
    if not static:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/api")
def api_root():
    return {"message": "Shresth Masala Industry Management API", "version": "0.1.0"}


# Auto-backup scheduler
def start_auto_backup_scheduler():
    """Start the auto-backup scheduler thread."""
    import threading
    from backend.app.core.config import settings
    from backend.app.core.database import SessionLocal, engine
    from backend.app.models import ApplicationSetting, BackupRecord

    def run_backup():
        while True:
            # Check settings
            db = SessionLocal()
            try:
                setting = db.query(ApplicationSetting).filter(ApplicationSetting.key == "backup").first()
                if not setting or not setting.value.get("enabled", True):
                    return
                schedule_hour = setting.value.get("schedule_hour", 2)
            finally:
                db.close()

            # Wait until next schedule hour
            import time
            from datetime import datetime, timedelta
            now = datetime.now()
            next_run = now.replace(hour=schedule_hour, minute=0, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            wait_seconds = (next_run - now).total_seconds()
            time.sleep(wait_seconds)

            # Perform backup
            db = SessionLocal()
            try:
                from backend.app.core.database import engine as eng
                db_url = str(eng.url)
                if db_url.startswith("sqlite:///"):
                    db_path = db_url[10:]
                else:
                    return

                import os
                backup_dir = os.path.join(os.path.dirname(db_path), "backups")
                os.makedirs(backup_dir, exist_ok=True)

                import shutil
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_filename = f"masala_factory_{timestamp}.db"
                backup_path = os.path.join(backup_dir, backup_filename)

                shutil.copy2(db_path, backup_path)

                # Verify integrity
                import sqlite3
                conn = sqlite3.connect(backup_path)
                cursor = conn.cursor()
                cursor.execute("PRAGMA integrity_check")
                result = cursor.fetchone()
                conn.close()
                if result[0] != "ok":
                    os.remove(backup_path)
                    return

                file_size = os.path.getsize(backup_path)
                record = BackupRecord(
                    filename=backup_filename,
                    file_size=file_size,
                    created_by=None,  # System backup
                )
                db.add(record)
                db.commit()

                # Prune old backups
                retention_days = setting.value.get("retention_days", 30)
                cutoff = datetime.now() - timedelta(days=retention_days)
                old_backups = db.query(BackupRecord).filter(BackupRecord.created_at < cutoff).all()
                for old in old_backups:
                    old_path = os.path.join(backup_dir, old.filename)
                    if os.path.exists(old_path):
                        os.remove(old_path)
                    db.delete(old)
                db.commit()
            except Exception:
                pass
            finally:
                db.close()

    thread = threading.Thread(target=run_backup, daemon=True)
    thread.start()


if __name__ == "__main__":
    import uvicorn

    # Loopback only. This process holds the full business database with no
    # authentication in front of the static frontend, so binding every
    # interface would put it on the LAN. Use main_window.py (the packaged
    # entry point) for the desktop app; this path is for debugging.
    uvicorn.run(app, host="127.0.0.1", port=8000)