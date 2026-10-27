"""Dashboard, notifications, share routes"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.shared import (
    _audit,
    _challan_entry_allowed,
    _current_user,
    _ist_wall,
    _low_finished_sizes,
    _request_payload,
    _request_user_and_role,
)
from backend.app.models import (
    Customer,
    CustomerPayment,
    DashboardPreference,
    DashboardShare,
    Employee,
    Expense,
    FinishedProductStockMovement,
    NotificationLog,
    PackagingSize,
    ProductionEntry,
    PurchaseInvoice,
    RawMaterial,
    RawMaterialStockMovement,
    SalesChallan,
    SalesInvoice,
    Supplier,
    SupplierPayment,
    User,
)
from backend.app.models.finished_product import FinishedProductMovementType
from backend.app.services import challan_snapshot as _snap

router = APIRouter()

# Receivable aging buckets, in days overdue (IST wall clock vs challan date).
RECEIVABLE_AGING_D30_DAYS = 30
RECEIVABLE_AGING_D60_DAYS = 60


@router.get("/dashboard/stats")
def get_dashboard_stats(db: Session = Depends(get_db)):
    """Dashboard cards: stocks, pending, sales this month, dues.

    Returns a flat dict matching the dashboard's fetchStats() contract so every
    card has a value instead of a 500.
    """

    now = _ist_wall()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month_end = month_start
    last_month_start = (month_start - timedelta(days=1)).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    today = now.date()

    def in_month(dt):
        if dt is None:
            return False
        try:
            naive = dt.replace(tzinfo=None) if getattr(dt, "tzinfo", None) else dt
        except Exception:
            naive = dt
        return naive >= month_start

    # Raw material stock
    total_raw = db.query(func.sum(RawMaterial.current_stock)).scalar() or Decimal("0")
    low_stock_count = (
        db.query(RawMaterial)
        .filter(
            RawMaterial.current_stock <= RawMaterial.threshold_stock,
            RawMaterial.is_active == True,  # noqa: E712
            RawMaterial.watch_stock == True,  # noqa: E712
        )
        .count()
    )
    # Finished packs at/below their per-size packet threshold (list of dicts).
    low_pack_alerts = len(_low_finished_sizes(db))

    # Raw stock change this month = purchased qty - consumed qty
    purchased_month = Decimal("0")
    consumed_month = Decimal("0")
    for m in (
        db.query(RawMaterialStockMovement)
        .filter(RawMaterialStockMovement.movement_type == "purchase")
        .all()
    ):
        if in_month(m.created_at):
            purchased_month += m.quantity or Decimal("0")
    for m in (
        db.query(RawMaterialStockMovement)
        .filter(RawMaterialStockMovement.movement_type == "consumption")
        .all()
    ):
        if in_month(m.created_at):
            consumed_month += m.quantity or Decimal("0")

    # Finished stock: produced vs sold this month and all-time balance (kg)
    produced_month = Decimal("0")
    sold_month = Decimal("0")
    produced_all = Decimal("0")
    sold_all = Decimal("0")
    prod_type = FinishedProductMovementType.PRODUCTION
    sale_type = FinishedProductMovementType.SALES
    for m in (
        db.query(FinishedProductStockMovement)
        .filter(FinishedProductStockMovement.movement_type == prod_type)
        .all()
    ):
        q = m.quantity or Decimal("0")
        produced_all += q
        if in_month(m.created_at):
            produced_month += q
    for m in (
        db.query(FinishedProductStockMovement)
        .filter(FinishedProductStockMovement.movement_type == sale_type)
        .all()
    ):
        q = m.quantity or Decimal("0")
        sold_all += q
        if in_month(m.created_at):
            sold_month += q
    finished_kg = produced_all - sold_all

    # Finished packets: per-size balance (kg) divided by that size's weight.
    finished_packets = Decimal("0")
    balance_by_size = defaultdict(Decimal)
    for m in db.query(FinishedProductStockMovement).all():
        q = m.quantity or Decimal("0")
        if m.movement_type == FinishedProductMovementType.PRODUCTION:
            balance_by_size[m.packaging_size_id] += q
        elif m.movement_type == FinishedProductMovementType.SALES:
            balance_by_size[m.packaging_size_id] -= q
    for size in db.query(PackagingSize).filter(PackagingSize.is_active == True).all():  # noqa: E712
        bal = balance_by_size.get(size.id, Decimal("0"))
        if bal > 0 and size.weight_in_kg:
            finished_packets += bal / size.weight_in_kg

    # Sales this / last month
    sales_this_month = (
        db.query(func.sum(SalesChallan.total_price))
        .filter(
            SalesChallan.challan_date >= month_start,
            SalesChallan.is_archived == False,  # noqa: E712
        )
        .scalar()
        or Decimal("0")
    )
    sales_last_month = (
        db.query(func.sum(SalesChallan.total_price))
        .filter(
            SalesChallan.challan_date >= last_month_start,
            SalesChallan.challan_date < last_month_end,
            SalesChallan.is_archived == False,  # noqa: E712
        )
        .scalar()
        or Decimal("0")
    )

    # Dues / receivables (unpaid challan balances)
    dues = _snap.all_challan_balances(db)
    total_dues = sum(b for b in dues.values() if b > 0)
    open_receivables = sum(1 for b in dues.values() if b > 0)
    d30 = d60 = over60 = 0
    for ch in db.query(SalesChallan).filter(SalesChallan.is_archived == False).all():  # noqa: E712
        bal = dues.get(ch.id, 0)
        if bal <= 0:
            continue
        days = (now - ch.challan_date).days if ch.challan_date else 0
        if days <= RECEIVABLE_AGING_D30_DAYS:
            d30 += 1
        elif days <= RECEIVABLE_AGING_D60_DAYS:
            d60 += 1
        else:
            over60 += 1
    receivables_aging = {"d30": d30, "d60": d60, "over60": over60}

    # Supplier payable (unpaid purchase invoices)
    supplier_payable = (
        db.query(func.sum(PurchaseInvoice.outstanding_amount))
        .filter(PurchaseInvoice.is_archived == False)  # noqa: E712
        .scalar()
        or Decimal("0")
    )

    # Payable aging (unpaid purchase invoices by days overdue)
    payables_due = db.query(PurchaseInvoice).filter(
        PurchaseInvoice.is_archived == False,  # noqa: E712
        PurchaseInvoice.outstanding_amount > 0
    ).all()

    payables_aging = {"d30": 0, "d60": 0, "over60": 0}
    payables_overdue = {"count": 0, "amount": 0.0}
    for inv in payables_due:
        days = (now - inv.invoice_date).days if inv.invoice_date else 0
        if days <= 30:
            payables_aging["d30"] += 1
        elif days <= 60:
            payables_aging["d60"] += 1
        else:
            payables_aging["over60"] += 1
        if days > 0:
            payables_overdue["count"] += 1
            payables_overdue["amount"] += float(inv.outstanding_amount or 0)

    receivables_overdue = d60 + over60

    # Ready to dispatch: confirmed/paid challans not yet dispatched or delivered
    ready_to_dispatch = (
        db.query(SalesChallan)
        .filter(
            SalesChallan.is_archived == False,  # noqa: E712
            SalesChallan.status.in_(("confirmed", "paid")),
            SalesChallan.dispatched_at == None,  # noqa: E712
            SalesChallan.delivered_at == None,   # noqa: E712
        )
        .count()
    )

    # Pending sales invoices: invoices whose challan still owes money
    pending_invoices_count = 0
    pending_invoices_amount = Decimal("0")
    for inv in db.query(SalesInvoice).filter(SalesInvoice.is_archived == False).all():  # noqa: E712
        ch = inv.challan
        if ch and ch.outstanding_amount and ch.outstanding_amount > 0:
            pending_invoices_count += 1
            pending_invoices_amount += ch.outstanding_amount or Decimal("0")

    # Cashflow this month (received vs paid)
    received = (
        db.query(func.sum(CustomerPayment.amount))
        .filter(CustomerPayment.is_archived == False)  # noqa: E712
        .filter(CustomerPayment.payment_date >= month_start)
        .scalar()
        or Decimal("0")
    )
    paid = (
        db.query(func.sum(Expense.amount))
        .filter(Expense.is_archived == False)  # noqa: E712
        .filter(Expense.date >= month_start.date())
        .scalar()
        or Decimal("0")
    ) + (
        db.query(func.sum(SupplierPayment.amount))
        .filter(SupplierPayment.is_archived == False)  # noqa: E712
        .filter(SupplierPayment.payment_date >= month_start)
        .scalar()
        or Decimal("0")
    )
    # Lifetime net cashflow (all non-archived received minus all non-archived paid)
    lifetime_received = (
        db.query(func.sum(CustomerPayment.amount))
        .filter(CustomerPayment.is_archived == False)  # noqa: E712
        .scalar()
        or Decimal("0")
    )
    lifetime_paid = (
        db.query(func.sum(Expense.amount))
        .filter(Expense.is_archived == False)  # noqa: E712
        .scalar()
        or Decimal("0")
    ) + (
        db.query(func.sum(SupplierPayment.amount))
        .filter(SupplierPayment.is_archived == False)  # noqa: E712
        .scalar()
        or Decimal("0")
    )
    cashflow_net = lifetime_received - lifetime_paid

    # Low stock items: raw materials below threshold + finished sizes below threshold
    low_stock_items = low_stock_count + low_pack_alerts

    # Production today (kg), created today on the IST wall clock
    production_today_kg = (
        db.query(func.sum(ProductionEntry.target_quantity))
        .filter(
            ProductionEntry.created_at >= today,
            ProductionEntry.created_at < today + timedelta(days=1),
            ProductionEntry.is_archived == False,  # noqa: E712
        )
        .scalar()
        or Decimal("0")
    )

    return {
        "raw_material_stock_kg": float(total_raw),
        "low_stock_count": low_stock_count,
        "raw_material_purchased_month": float(purchased_month),
        "raw_material_consumed_month": float(consumed_month),
        "finished_goods_packets": float(finished_packets),
        "finished_goods_kg": float(finished_kg),
        "finished_produced_month": float(produced_month),
        "finished_sold_month": float(sold_month),
        "sales_this_month": float(sales_this_month),
        "sales_last_month": float(sales_last_month),
        "customer_outstanding": float(total_dues),
        "outstanding_total": float(total_dues),
        "open_receivables": open_receivables,
        "receivables_aging": receivables_aging,
        "supplier_payable": float(supplier_payable),
        "payables_aging": payables_aging,
        "payables_overdue": payables_overdue,
        "receivables_overdue": receivables_overdue,
        "ready_to_dispatch": ready_to_dispatch,
        "pending_invoices_count": pending_invoices_count,
        "pending_invoices_amount": float(pending_invoices_amount),
        "cashflow_month": {"received": float(received), "paid": float(paid)},
        "cashflow_net": float(cashflow_net),
        "low_pack_alerts": low_pack_alerts,
        "low_stock_items": low_stock_items,
        "production_today_kg": float(production_today_kg),
    }


@router.get("/dashboard/production-today")
def production_today(db: Session = Depends(get_db)):
    """Production entries created today (IST wall clock)."""
    from datetime import date
    today = _ist_wall().date()
    entries = (
        db.query(ProductionEntry)
        .filter(
            ProductionEntry.created_at >= today,
            ProductionEntry.created_at < today + timedelta(days=1),
            ProductionEntry.is_archived == False,  # noqa: E712
        )
        .order_by(ProductionEntry.created_at.desc())
        .all()
    )
    return [
        {
            "id": e.id,
            "production_number": e.production_number,
            "product_id": e.product_id,
            "product_name": e.product.name if e.product else None,
            "status": e.status.value if e.status else None,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in entries
    ]


@router.get("/api/dashboard/preferences", response_model=dict)
def list_dashboard_preferences(request: Request, db: Session = Depends(get_db)):
    user, _ = _request_user_and_role(db, request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    pref = (
        db.query(DashboardPreference)
        .filter(DashboardPreference.user_id == user.id)
        .first()
    )
    if not pref:
        return {
            "show_sales": True, "show_production": True, "show_inventory": True,
            "show_outstanding": True, "show_purchases": True, "show_expenses": True,
            "show_employees": True, "show_recent_transactions": True,
            "theme": "light",
        }
    return {
        "show_sales": bool(pref.show_sales),
        "show_production": bool(pref.show_production),
        "show_inventory": bool(pref.show_inventory),
        "show_outstanding": bool(pref.show_outstanding),
        "show_purchases": bool(pref.show_purchases),
        "show_expenses": bool(pref.show_expenses),
        "show_employees": bool(pref.show_employees),
        "show_recent_transactions": bool(pref.show_recent_transactions),
        "theme": pref.theme or "light",
    }


@router.put("/api/dashboard/preferences", response_model=dict)
def upsert_dashboard_preference(
    payload: dict, request: Request, db: Session = Depends(get_db)
):
    user, _ = _request_user_and_role(db, request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    pref = (
        db.query(DashboardPreference)
        .filter(DashboardPreference.user_id == user.id)
        .first()
    )
    if not pref:
        pref = DashboardPreference(user_id=user.id)
        db.add(pref)
    for key in (
        "show_sales", "show_production", "show_inventory", "show_outstanding",
        "show_purchases", "show_expenses", "show_employees",
        "show_recent_transactions",
    ):
        if key in (payload or {}):
            setattr(pref, key, bool(payload[key]))
    if "theme" in (payload or {}):
        pref.theme = payload["theme"]
    db.commit()
    db.refresh(pref)
    return {
        "show_sales": bool(pref.show_sales),
        "show_production": bool(pref.show_production),
        "show_inventory": bool(pref.show_inventory),
        "show_outstanding": bool(pref.show_outstanding),
        "show_purchases": bool(pref.show_purchases),
        "show_expenses": bool(pref.show_expenses),
        "show_employees": bool(pref.show_employees),
        "show_recent_transactions": bool(pref.show_recent_transactions),
        "theme": pref.theme or "light",
    }


@router.get("/notifications/recent")
def recent_notifications(
    limit: int = 100,
    request: Request = None,
    db: Session = Depends(get_db),
):
    user, _ = _request_user_and_role(db, request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    rows = (
        db.query(NotificationLog)
        .filter(NotificationLog.recipient == user.username)
        .order_by(NotificationLog.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": n.id,
            "message": n.message,
            "status": n.status,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        }
        for n in rows
    ]


@router.get("/notifications/unread")
def unread_notifications_count(request: Request, db: Session = Depends(get_db)):
    user, _ = _request_user_and_role(db, request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    count = (
        db.query(NotificationLog)
        .filter(NotificationLog.recipient == user.username, NotificationLog.status == "unread")
        .count()
    )
    return {"count": count}


@router.post("/notifications/client-log")
def client_log(payload: dict, request: Request, db: Session = Depends(get_db)):
    """Receive a client-side log entry (no auth required)."""
    # This is intentionally open so a pre-login script error can still be recorded.
    level = (payload or {}).get("level") or "info"
    message = (payload or {}).get("message") or ""
    if not message:
        raise HTTPException(status_code=400, detail="message required")
    # Store with a generic recipient so it doesn't pollute per-user lists.
    db.add(NotificationLog(recipient="client", message=f"[{level}] {message}", status="client-log"))
    db.commit()
    return {"status": "logged"}


@router.delete("/notifications/recent")
def clear_notifications(request: Request, db: Session = Depends(get_db)):
    user, _ = _request_user_and_role(db, request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    deleted = (
        db.query(NotificationLog)
        .filter(NotificationLog.recipient == user.username)
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"status": "cleared", "count": deleted}


@router.get("/dashboard/share/{link_id}")
def get_dashboard_share(link_id: str, db: Session = Depends(get_db)):
    """Public dashboard share link - no auth required."""
    link = (
        db.query(DashboardShare)
        .filter(DashboardShare.link_id == link_id, DashboardShare.is_active == True)  # noqa: E712
        .first()
    )
    if not link:
        raise HTTPException(status_code=404, detail="Share link not found or expired")
    # Check expiry
    if link.expires_at and _ist_wall() > link.expires_at:
        raise HTTPException(status_code=410, detail="Share link has expired")
    # Return the saved dashboard config
    return {"config": link.config_json, "page": link.page_key}


@router.post("/dashboard/share")
def create_dashboard_share(payload: dict, request: Request, db: Session = Depends(get_db)):
    """Create a public dashboard share link (admin/manager)."""
    user, role = _request_user_and_role(db, request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    # rbac_guard enforces admin/manager for POST /dashboard/share
    page_key = (payload or {}).get("page_key")
    config = (payload or {}).get("config")
    expires_days = (payload or {}).get("expires_days")
    if not page_key or config is None:
        raise HTTPException(status_code=400, detail="page_key and config are required")
    import uuid
    from datetime import timedelta
    link_id = uuid.uuid4().hex[:12]
    expires_at = None
    if expires_days:
        try:
            expires_at = _ist_wall() + timedelta(days=int(expires_days))
        except (TypeError, ValueError):
            pass
    link = DashboardShare(
        link_id=link_id,
        page_key=page_key,
        config_json=config,
        created_by=user.id,
        expires_at=expires_at,
    )
    db.add(link)
    _audit(
        db,
        request,
        "share",
        "create",
        record=link,
        record_type="dashboard_share",
        new_values={"link_id": link_id, "page_key": page_key},
    )
    db.commit()
    return {"link_id": link_id, "url": f"/share/{link_id}"}


@router.get("/dashboard/shares")
def list_dashboard_shares(request: Request, db: Session = Depends(get_db)):
    """List all share links (admin/manager)."""
    user, role = _request_user_and_role(db, request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    # rbac_guard enforces admin/manager for GET /dashboard/shares
    links = db.query(DashboardShare).order_by(DashboardShare.created_at.desc()).all()
    return [
        {
            "link_id": l.link_id,
            "page_key": l.page_key,
            "created_at": l.created_at.isoformat() if l.created_at else None,
            "expires_at": l.expires_at.isoformat() if l.expires_at else None,
            "is_active": l.is_active,
            "created_by": l.created_by,
        }
        for l in links
    ]


@router.delete("/dashboard/shares/{link_id}")
def revoke_dashboard_share(link_id: str, request: Request, db: Session = Depends(get_db)):
    """Revoke a public dashboard share link (admin/manager)."""
    link = db.query(DashboardShare).filter(DashboardShare.link_id == link_id).first()
    if not link:
        raise HTTPException(status_code=404, detail="Share link not found")
    link.is_active = False
    _audit(
        db,
        request,
        "share",
        "revoke",
        record=link,
        record_type="dashboard_share",
        old_values={"is_active": True},
        new_values={"is_active": False, "link_id": link_id},
    )
    db.commit()
    return {"status": "revoked", "link_id": link_id}