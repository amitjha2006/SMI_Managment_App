"""Report routes: raw material summary, purchase summary, payment summary, production history, profit-loss, summary, recent, sales trend, custom reports, saved reports"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import and_, func
from sqlalchemy.orm import Session
from datetime import datetime, date, timedelta, time
from decimal import Decimal

from backend.app.core.database import get_db
from backend.app.models import (
    Customer,
    CustomerPayment,
    Expense,
    ExpenseCategory,
    PackagingSize,
    Product,
    ProductionEntry,
    ProductionStageEntry,
    PurchaseInvoice,
    PurchaseInvoiceItem,
    RawMaterial,
    RawMaterialStockMovement,
    SalesChallan,
    SalesChallanItem,
    SavedReport,
    SalaryPayment,
    Supplier,
    SupplierPayment,
    FinishedProductStockMovement,
)
from backend.app.schemas import SavedReportSchema
from backend.app.core.shared import (
    _audit,
    _change_reason,
    _current_user,
    _ist_wall,
    _low_finished_sizes,
    _reject_if_archived,
    _archive_record,
    _restore_record,
    _request_user_and_role,
)
from backend.app.services import challan_snapshot as _snap

router = APIRouter()


# Raw material summary
@router.get("/reports/raw-material-summary")
def raw_material_summary(
    db: Session = Depends(get_db),
    from_date: str | None = None,
    to_date: str | None = None,
):
    """Raw material consumption vs purchase summary.

    Stock = opening_stock + confirmed in-range purchases + in-range adjustments.
    Production = in-range consumed raw material (archived production excluded).
    Wastage = stage-entry wastage allocated pro-rata over what each batch consumed.
    Returns {rows, from, to, totals}.
    """
    fdate = datetime.fromisoformat(from_date).date() if from_date else None
    tdate = datetime.fromisoformat(to_date).date() if to_date else None

    materials = db.query(RawMaterial).filter(RawMaterial.is_active == True).all()  # noqa: E712

    # ---- confirmed, non-archived purchases in range ----
    pq = (
        db.query(PurchaseInvoiceItem.raw_material_id, func.sum(PurchaseInvoiceItem.quantity).label("qty"))
        .join(PurchaseInvoice)
        .filter(PurchaseInvoice.is_confirmed == True, PurchaseInvoice.is_archived == False)  # noqa: E712
    )
    if fdate:
        pq = pq.filter(PurchaseInvoice.invoice_date >= datetime.combine(fdate, time.min))
    if tdate:
        pq = pq.filter(PurchaseInvoice.invoice_date <= datetime.combine(tdate, time.max))
    purchases = {r.raw_material_id: float(r.qty or 0) for r in pq.group_by(PurchaseInvoiceItem.raw_material_id).all()}

    # ---- adjustments in range (filter on created_at; movement_date is NULL here) ----
    aq = db.query(
        RawMaterialStockMovement.raw_material_id,
        func.sum(RawMaterialStockMovement.quantity).label("qty"),
    ).filter(RawMaterialStockMovement.movement_type == "adjustment")
    if fdate:
        aq = aq.filter(RawMaterialStockMovement.created_at >= datetime.combine(fdate, time.min))
    if tdate:
        aq = aq.filter(RawMaterialStockMovement.created_at <= datetime.combine(tdate, time.max))
    adjustments = {r.raw_material_id: float(r.qty or 0) for r in aq.group_by(RawMaterialStockMovement.raw_material_id).all()}

    # ---- consumption movements in range, excluding archived production entries ----
    cq = (
        db.query(
            RawMaterialStockMovement.raw_material_id,
            RawMaterialStockMovement.reference_id,
            func.sum(RawMaterialStockMovement.quantity).label("qty"),
        )
        .filter(
            RawMaterialStockMovement.movement_type == "consumption",
            RawMaterialStockMovement.reference_type == "production_entry",
        )
        .join(
            ProductionEntry,
            and_(
                ProductionEntry.id == RawMaterialStockMovement.reference_id,
                ProductionEntry.is_archived == False,  # noqa: E712
            ),
        )
    )
    if fdate:
        cq = cq.filter(RawMaterialStockMovement.movement_date >= fdate)
    if tdate:
        cq = cq.filter(RawMaterialStockMovement.movement_date <= tdate)
    consumption_rows = cq.group_by(
        RawMaterialStockMovement.raw_material_id, RawMaterialStockMovement.reference_id
    ).all()

    consumption = {}          # material_id -> total consumed kg
    entry_material = {}       # entry_id -> {material_id: qty}
    entry_total = {}          # entry_id -> total consumed kg
    for mat_id, entry_id, qty in consumption_rows:
        q = float(qty or 0)
        consumption[mat_id] = consumption.get(mat_id, 0) + q
        entry_material.setdefault(entry_id, {})[mat_id] = q
        entry_total[entry_id] = entry_total.get(entry_id, 0) + q

    # ---- stage-entry wastage per entry (in range, non-archived) ----
    sq = (
        db.query(ProductionStageEntry.production_entry_id, func.sum(ProductionStageEntry.wastage_quantity).label("wastage"))
        .join(
            ProductionEntry,
            and_(
                ProductionEntry.id == ProductionStageEntry.production_entry_id,
                ProductionEntry.is_archived == False,  # noqa: E712
            ),
        )
    )
    if fdate:
        sq = sq.filter(ProductionEntry.production_date >= datetime.combine(fdate, time.min))
    if tdate:
        sq = sq.filter(ProductionEntry.production_date <= datetime.combine(tdate, time.max))
    stage_wastage = {r.production_entry_id: float(r.wastage or 0) for r in sq.group_by(ProductionStageEntry.production_entry_id).all()}

    # ---- allocate stage wastage pro-rata over each batch's consumed materials ----
    wastage = {}
    for entry_id, mats in entry_material.items():
        total = entry_total[entry_id]
        if total <= 0:
            continue
        sw = stage_wastage.get(entry_id, 0)
        if sw <= 0:
            continue
        for mat_id, q in mats.items():
            wastage[mat_id] = wastage.get(mat_id, 0) + (q / total) * sw

    rows = []
    for m in materials:
        opening = float(m.opening_stock or 0)
        stock = round(opening + purchases.get(m.id, 0) + adjustments.get(m.id, 0), 2)
        production = round(consumption.get(m.id, 0), 2)
        balance = round(stock - production, 2)
        wkg = round(wastage.get(m.id, 0), 2)
        wpct = round(wkg / production * 100, 2) if production else 0.0
        rows.append({
            "name": m.name,
            "stock_kg": stock,
            "production_kg": production,
            "balance_kg": balance,
            "wastage_kg": wkg,
            "wastage_pct": wpct,
        })

    rows.sort(key=lambda r: r["name"] or "")
    totals = {
        "stock_kg": round(sum(r["stock_kg"] for r in rows), 2),
        "production_kg": round(sum(r["production_kg"] for r in rows), 2),
        "balance_kg": round(sum(r["balance_kg"] for r in rows), 2),
        "wastage_kg": round(sum(r["wastage_kg"] for r in rows), 2),
    }
    totals["wastage_pct"] = round(totals["wastage_kg"] / totals["production_kg"] * 100, 2) if totals["production_kg"] else 0.0
    return {"rows": rows, "from": from_date or "", "to": to_date or "", "totals": totals}


# Stock health report
@router.get("/reports/summary")
def reports_summary(
    from_date: str | None = None,
    to_date: str | None = None,
    db: Session = Depends(get_db),
):
    """Low-stock raw materials: current_stock <= threshold_stock."""
    q = db.query(RawMaterial).filter(RawMaterial.is_active == True)  # noqa: E712
    low_stock = [
        {"name": m.name, "current_stock": float(m.current_stock or 0), "threshold_stock": float(m.threshold_stock or 0)}
        for m in q.filter(RawMaterial.watch_stock == True).all()  # noqa: E712
        if float(m.current_stock or 0) <= float(m.threshold_stock or 0)
    ]
    low_packs = [
        {"name": ls["size_name"], "current_stock": ls["packets"], "threshold_stock": ls["threshold_packets"]}
        for ls in _low_finished_sizes(db)
    ]
    return {"low_stock": low_stock, "low_packs": low_packs}


# Recent transactions: newest first, expenses included.
@router.get("/reports/recent-transactions")
def recent_transactions(
    limit: int = 100,
    db: Session = Depends(get_db),
    from_date: str | None = None,
    to_date: str | None = None,
):
    """Recent activity rows across challans, purchases, payments and expenses.

    Each row: {type, id, date, amount, reference, created_at}. Newest first by
    recorded time (created_at), ties broken by id desc — same-day business dates
    tie at midnight, so recorded time decides the order.
    """
    fdate = None
    tdate = None
    tdate_end = None
    if from_date:
        try:
            fdate = datetime.fromisoformat(from_date).date()
        except ValueError:
            fdate = None
    if to_date:
        try:
            tdate = datetime.fromisoformat(to_date).date()
            tdate_end = datetime.combine(tdate + timedelta(days=1), time.min)
        except ValueError:
            tdate = None

    rows = []

    q = db.query(SalesChallan).filter(SalesChallan.is_archived == False)  # noqa: E712
    if fdate:
        q = q.filter(SalesChallan.challan_date >= fdate)
    if tdate_end:
        q = q.filter(SalesChallan.challan_date < tdate_end)
    for ch in q.all():
        cust = ch.customer
        rows.append({
            "type": "Challan",
            "id": ch.id,
            "date": ch.challan_date.isoformat() if ch.challan_date else None,
            "amount": float(ch.total_price or 0),
            "reference": ch.challan_number or "",
            "created_at": ch.created_at.isoformat() if ch.created_at else None,
        })

    q = db.query(PurchaseInvoice).filter(PurchaseInvoice.is_archived == False)  # noqa: E712
    if fdate:
        q = q.filter(PurchaseInvoice.invoice_date >= fdate)
    if tdate_end:
        q = q.filter(PurchaseInvoice.invoice_date < tdate_end)
    for inv in q.all():
        sup = inv.supplier
        rows.append({
            "type": "Purchase",
            "id": inv.id,
            "date": inv.invoice_date.isoformat() if inv.invoice_date else None,
            "amount": float(inv.total_amount or 0),
            "reference": inv.invoice_number or "",
            "created_at": inv.created_at.isoformat() if inv.created_at else None,
        })

    q = db.query(CustomerPayment).filter(CustomerPayment.is_archived == False)  # noqa: E712
    if fdate:
        q = q.filter(CustomerPayment.payment_date >= fdate)
    if tdate:
        q = q.filter(CustomerPayment.payment_date <= tdate)
    for p in q.all():
        ch = p.challan
        cust = ch.customer if ch else None
        rows.append({
            "type": "Payment",
            "id": p.id,
            "date": p.payment_date.isoformat() if p.payment_date else None,
            "amount": float(p.amount or 0),
            "reference": ch.challan_number if ch else "",
            "created_at": p.created_at.isoformat() if p.created_at else None,
        })

    q = db.query(Expense).filter(Expense.is_archived == False)  # noqa: E712
    if fdate:
        q = q.filter(Expense.date >= fdate)
    if tdate_end:
        q = q.filter(Expense.date < tdate_end)
    for e in q.all():
        cat = e.category
        rows.append({
            "type": "Expense",
            "id": e.id,
            "date": e.date.isoformat() if e.date else None,
            "amount": float(e.amount or 0),
            "reference": e.description or "",
            "created_at": e.created_at.isoformat() if e.created_at else None,
        })

    rows.sort(key=lambda r: (r["created_at"] or "", r["id"] or 0), reverse=True)
    return rows[:limit]
@router.get("/reports/purchase-summary")
def purchase_summary(
    db: Session = Depends(get_db),
    from_date: str | None = None,
    to_date: str | None = None,
    supplier_id: int | None = None,
):
    """Purchase invoice summary.

    Returns {rows, from, to} where each row is a raw material with
    purchased_kg and purchase_amount aggregated across confirmed invoices.
    Fields match what the frontend expects: name, purchased_kg,
    purchase_amount.
    """
    q = db.query(PurchaseInvoice).filter(
        PurchaseInvoice.is_archived == False,  # noqa: E712
        PurchaseInvoice.is_confirmed == True,  # noqa: E712
    )
    if from_date:
        q = q.filter(PurchaseInvoice.invoice_date >= datetime.fromisoformat(from_date).date())
    if to_date:
        q = q.filter(PurchaseInvoice.invoice_date <= datetime.fromisoformat(to_date).date())
    if supplier_id:
        q = q.filter(PurchaseInvoice.supplier_id == supplier_id)

    invoices = q.all()

    materials = db.query(RawMaterial).filter(RawMaterial.is_active == True).all()  # noqa: E712

    # Aggregate per raw material across all invoices in range.
    agg = {}
    for inv in invoices:
        for item in inv.items:
            key = item.raw_material_id
            if key not in agg:
                agg[key] = {
                    "name": item.raw_material.name if item.raw_material else "Unknown",
                    "qty": 0.0,
                    "amount": 0.0,
                }
            agg[key]["qty"] += float(item.quantity or 0)
            agg[key]["amount"] += float(item.amount or 0)

    rows = []
    for m in materials:
        purchased_kg = 0.0
        purchase_amount = 0.0
        for inv in invoices:
            for item in inv.items:
                if item.raw_material_id == m.id:
                    purchased_kg += float(item.quantity or 0)
                    purchase_amount += float(item.amount or 0)
        rows.append({
            "name": m.name,
            "purchased_kg": purchased_kg,
            "purchase_amount": purchase_amount,
        })
    rows.sort(key=lambda r: r["name"] or "")
    totals = {
        "materials": len(rows),
        "purchased_kg": round(sum(r["purchased_kg"] for r in rows), 2),
        "purchase_amount": round(sum(r["purchase_amount"] for r in rows), 2),
    }
    return {"rows": rows, "from": from_date or "", "to": to_date or "", "totals": totals}


# Payment summary
@router.get("/reports/payment-summary")
def payment_summary(
    db: Session = Depends(get_db),
    from_date: str | None = None,
    to_date: str | None = None,
):
    """Customer payment summary.

    Returns {receipts, from, to} where each receipt matches the fields the
    frontend reads: customer_name, challan_number, payment_mode,
    transaction_ref, amount, payment_date, created_at, challan_id, id.
    """
    q = db.query(CustomerPayment).filter(CustomerPayment.is_archived == False)  # noqa: E712
    if from_date:
        q = q.filter(CustomerPayment.payment_date >= datetime.fromisoformat(from_date).date())
    if to_date:
        q = q.filter(CustomerPayment.payment_date <= datetime.fromisoformat(to_date).date())

    payments = q.order_by(CustomerPayment.payment_date.desc()).all()

    # Per-customer aggregation (zeros included for active customers).
    customer_rows = {}
    for p in payments:
        if not p.challan or not p.challan.customer:
            continue
        cid = p.challan.customer.id
        amt = float(p.amount or 0)
        if cid not in customer_rows:
            customer_rows[cid] = {
                "name": p.challan.customer.name,
                "receipts": 0,
                "received_amount": 0.0,
            }
        customer_rows[cid]["receipts"] += 1
        customer_rows[cid]["received_amount"] += amt

    all_customers = db.query(Customer).filter(Customer.is_active == True).all()  # noqa: E712
    rows = []
    for c in all_customers:
        if c.id in customer_rows:
            rows.append(customer_rows[c.id])
        else:
            rows.append({"name": c.name, "receipts": 0, "received_amount": 0.0})
    rows.sort(key=lambda r: r["name"] or "")

    totals_receipts = sum(r["receipts"] for r in rows)
    totals_amount = round(sum(r["received_amount"] for r in rows), 2)
    by_mode = {}
    for p in payments:
        mode = p.payment_mode or "cash"
        by_mode[mode] = round(by_mode.get(mode, 0.0) + float(p.amount or 0), 2)

    receipts = []
    for p in payments:
        receipts.append({
            "id": p.id,
            "challan_id": p.challan_id,
            "customer_name": p.challan.customer.name if p.challan and p.challan.customer else None,
            "challan_number": p.challan.challan_number if p.challan else None,
            "payment_mode": p.payment_mode,
            "transaction_ref": p.transaction_ref,
            "amount": float(p.amount or 0),
            "payment_date": p.payment_date.isoformat() if p.payment_date else None,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        })

    return {
        "rows": rows,
        "totals": {
            "receipts": totals_receipts,
            "received_amount": totals_amount,
            "by_mode": by_mode,
        },
        "receipts": receipts,
        "from": from_date or "",
        "to": to_date or "",
    }


# Production history
@router.get("/reports/production-history")
def production_history(
    db: Session = Depends(get_db),
    from_date: str | None = None,
    to_date: str | None = None,
    product_id: int | None = None,
):
    """Per-batch, per-material production ledger.

    Each row is one material in one non-archived batch: consumed kg, the
    batch's pro-rata stage wastage allocated to that material, processed kg
    (consumed minus wastage) and wastage %. Newest first; negatives are shown
    honestly, never clamped. Returns {rows, totals}.
    """
    fdate = datetime.fromisoformat(from_date).date() if from_date else None
    tdate = datetime.fromisoformat(to_date).date() if to_date else None
    q = db.query(ProductionEntry).filter(ProductionEntry.is_archived == False)  # noqa: E712
    if product_id:
        q = q.filter(ProductionEntry.product_id == product_id)
    if fdate:
        q = q.filter(ProductionEntry.production_date >= datetime.combine(fdate, time.min))
    if tdate:
        q = q.filter(ProductionEntry.production_date <= datetime.combine(tdate, time.max))
    entries = q.order_by(ProductionEntry.production_date.desc(), ProductionEntry.id.desc()).all()

    rows = []
    tot_batches = 0
    tot_consumed = Decimal("0")
    tot_wastage = Decimal("0")
    for e in entries:
        tot_batches += 1
        # Consumed kg per material for this batch.
        cm = (
            db.query(
                RawMaterialStockMovement.raw_material_id,
                RawMaterial.name.label("mat_name"),
                func.sum(RawMaterialStockMovement.quantity).label("qty"),
            )
            .join(RawMaterial, RawMaterial.id == RawMaterialStockMovement.raw_material_id)
            .filter(
                RawMaterialStockMovement.movement_type == "consumption",
                RawMaterialStockMovement.reference_type == "production_entry",
                RawMaterialStockMovement.reference_id == e.id,
            )
            .group_by(RawMaterialStockMovement.raw_material_id, RawMaterial.name)
            .order_by(RawMaterial.name)
            .all()
        )
        # Batch-level stage wastage and input.
        sw = (
            db.query(
                func.coalesce(func.sum(ProductionStageEntry.wastage_quantity), 0).label("wastage"),
                func.coalesce(func.sum(ProductionStageEntry.input_quantity), 0).label("input"),
            )
            .filter(ProductionStageEntry.production_entry_id == e.id)
            .first()
        )
        stage_wastage = float(sw.wastage or 0)
        stage_input = float(sw.input or 0)
        for mat_id, mat_name, qty in cm:
            consumed = float(qty or 0)
            wastage = consumed * (stage_wastage / stage_input) if stage_input > 0 else 0.0
            processed = consumed - wastage
            wpct = (wastage / consumed * 100) if consumed else 0.0
            rows.append({
                "production_number": e.production_number,
                "material": mat_name or "",
                "consumed_kg": round(consumed, 2),
                "wastage_kg": round(wastage, 2),
                "processed_kg": round(processed, 2),
                "wastage_pct": round(wpct, 2),
            })
            tot_consumed += Decimal(str(round(consumed, 2)))
            tot_wastage += Decimal(str(round(wastage, 2)))

    return {
        "rows": rows,
        "totals": {
            "batches": tot_batches,
            "consumed_kg": float(tot_consumed),
            "wastage_kg": float(tot_wastage),
        },
    }


# Profit & Loss
@router.get("/reports/profit-loss")
def profit_loss(
    db: Session = Depends(get_db),
    from_date: str | None = None,
    to_date: str | None = None,
    period: str | None = None,
):
    """Profit & Loss report, optionally filtered to a period ("YYYY-MM" or "YYYY")."""
    fdate = None
    tdate = None
    if period:
        if len(period) == 7:  # YYYY-MM
            y, mn = period.split("-")
            fdate = date(int(y), int(mn), 1)
            tdate = date(int(y), int(mn) + 1, 1) if int(mn) < 12 else date(int(y) + 1, 1, 1)
        elif len(period) == 4:  # YYYY
            fdate = date(int(period), 1, 1)
            tdate = date(int(period) + 1, 1, 1)
    elif from_date:
        fdate = datetime.fromisoformat(from_date).date()
    if to_date and not period:
        tdate = datetime.fromisoformat(to_date).date()

    def _in_range(q, col):
        if fdate is not None:
            q = q.filter(col >= fdate)
        if tdate is not None:
            q = q.filter(col < tdate)
        return q

    # Revenue: confirmed sales challans
    q_sales = _in_range(db.query(SalesChallan).filter(
        SalesChallan.status == "confirmed",
        SalesChallan.is_archived == False,  # noqa: E712
    ), SalesChallan.challan_date)
    revenue = sum(float(ch.total_price or 0) for ch in q_sales.all())

    # COGS: raw material consumption valued at purchase rate
    # Simplified: use purchase invoices in period
    q_purchases = _in_range(db.query(PurchaseInvoice).filter(PurchaseInvoice.is_archived == False),  # noqa: E712
                            PurchaseInvoice.invoice_date)
    cogs = sum(float(inv.total_amount or 0) for inv in q_purchases.all())

    # Expenses
    q_expenses = _in_range(db.query(Expense).filter(Expense.is_archived == False), Expense.date)
    expenses = sum(float(e.amount or 0) for e in q_expenses.all())

    # Payroll
    q_payroll = _in_range(db.query(SalaryPayment).filter(SalaryPayment.is_archived == False), SalaryPayment.payment_date)
    payroll = sum(float(p.net_pay or 0) for p in q_payroll.all())

    gross_profit = revenue - cogs
    net_profit = gross_profit - expenses - payroll

    period_str = period or (f"{from_date} to {to_date}" if from_date and to_date else "All time")

    return {
        "sales": revenue,
        "purchases": cogs,
        "expenses": expenses,
        "payroll": payroll,
        "profit": net_profit,
        "period": period_str,
        # Kept for backward compatibility with any other callers.
        "revenue": revenue,
        "cogs": cogs,
        "gross_profit": gross_profit,
        "net_profit": net_profit,
    }


# Summary report
@router.get("/reports/summary")
def summary_report(
    from_date: str | None = None,
    to_date: str | None = None,
    db: Session = Depends(get_db),
):
    """Executive summary report."""
    if from_date:
        dt_from = datetime.fromisoformat(from_date)
    else:
        dt_from = datetime.now() - timedelta(days=30)
    if to_date:
        dt_to = datetime.fromisoformat(to_date)
    else:
        dt_to = datetime.now()

    # Sales
    sales = db.query(SalesChallan).filter(
        SalesChallan.challan_date >= dt_from.date(),
        SalesChallan.challan_date <= dt_to.date(),
        SalesChallan.is_archived == False,  # noqa: E712
    ).all()
    total_sales = sum(float(ch.total_price or 0) for ch in sales)
    total_challans = len(sales)

    # Purchases
    purchases = db.query(PurchaseInvoice).filter(
        PurchaseInvoice.invoice_date >= dt_from.date(),
        PurchaseInvoice.invoice_date <= dt_to.date(),
        PurchaseInvoice.is_archived == False,  # noqa: E712
    ).all()
    total_purchases = sum(float(inv.total_amount or 0) for inv in purchases)

    # Collections
    collections = db.query(CustomerPayment).filter(
        CustomerPayment.payment_date >= dt_from.date(),
        CustomerPayment.payment_date <= dt_to.date(),
    ).all()
    total_collections = sum(float(p.amount or 0) for p in collections)

    # Supplier payments
    sup_payments = db.query(SupplierPayment).filter(
        SupplierPayment.payment_date >= dt_from.date(),
        SupplierPayment.payment_date <= dt_to.date(),
    ).all()
    total_sup_payments = sum(float(p.amount or 0) for p in sup_payments)

    # Expenses
    expenses = db.query(Expense).filter(
        Expense.date >= dt_from.date(),
        Expense.date <= dt_to.date(),
        Expense.is_archived == False,  # noqa: E712
    ).all()
    total_expenses = sum(float(e.amount or 0) for e in expenses)

    # Dues
    dues = _snap.all_challan_balances(db)
    total_dues = sum(b for b in dues.values() if b > 0)

    return {
        "period": {"from": dt_from.date().isoformat(), "to": dt_to.date().isoformat()},
        "sales": {"count": total_challans, "amount": total_sales},
        "purchases": {"count": len(purchases), "amount": total_purchases},
        "collections": {"count": len(collections), "amount": total_collections},
        "supplier_payments": {"count": len(sup_payments), "amount": total_sup_payments},
        "expenses": {"count": len(expenses), "amount": total_expenses},
        "outstanding_dues": total_dues,
    }


# Recent activity
@router.get("/reports/recent")
def recent_activity(
    limit: int = 20,
    from_date: str | None = None,
    to_date: str | None = None,
    db: Session = Depends(get_db),
):
    """Recent activity across all modules.

    Supports optional ``from_date``/``to_date`` (ISO date strings) to filter
    on the business/recorded date of each row.
    """
    from datetime import datetime

    fdate = None
    tdate = None
    tdate_end = None
    if from_date:
        try:
            fdate = datetime.fromisoformat(from_date).date()
        except ValueError:
            fdate = None
    if to_date:
        try:
            tdate = datetime.fromisoformat(to_date).date()
            tdate_end = datetime.combine(tdate + timedelta(days=1), time.min)
        except ValueError:
            tdate = None

    activities = []

    # Recent challans
    q = db.query(SalesChallan).filter(SalesChallan.is_archived == False)  # noqa: E712
    if fdate:
        q = q.filter(SalesChallan.challan_date >= fdate)
    if tdate:
        q = q.filter(SalesChallan.challan_date <= tdate)
    challans = q.order_by(SalesChallan.id.desc()).limit(limit).all()
    for ch in challans:
        cust = ch.customer
        activities.append({
            "type": "challan",
            "id": ch.id,
            "title": f"Challan {ch.challan_number}",
            "reference": ch.challan_number,
            "party": cust.name if cust else "",
            "date": ch.challan_date.isoformat() if ch.challan_date else None,
            "amount": float(ch.total_price or 0),
            "status": ch.status.value if ch.status else "",
            "method": "",
        })

    # Recent purchase invoices
    q = db.query(PurchaseInvoice).filter(PurchaseInvoice.is_archived == False)  # noqa: E712
    if fdate:
        q = q.filter(PurchaseInvoice.invoice_date >= fdate)
    if tdate_end:
        q = q.filter(PurchaseInvoice.invoice_date < tdate_end)
    purchases = q.order_by(PurchaseInvoice.id.desc()).limit(limit).all()
    for inv in purchases:
        sup = inv.supplier
        activities.append({
            "type": "purchase",
            "id": inv.id,
            "title": f"Invoice {inv.invoice_number}",
            "reference": inv.invoice_number,
            "party": sup.name if sup else "",
            "date": inv.invoice_date.isoformat() if inv.invoice_date else None,
            "amount": float(inv.total_amount or 0),
            "status": inv.payment_status.value if inv.payment_status else "",
            "method": "",
        })

    # Recent customer payments
    q = db.query(CustomerPayment).filter(CustomerPayment.is_archived == False)  # noqa: E712
    if fdate:
        q = q.filter(CustomerPayment.payment_date >= fdate)
    if tdate:
        q = q.filter(CustomerPayment.payment_date <= tdate)
    payments = q.order_by(CustomerPayment.id.desc()).limit(limit).all()
    for p in payments:
        ch = p.challan
        cust = ch.customer if ch else None
        activities.append({
            "type": "payment",
            "id": p.id,
            "title": f"Receipt {p.id:04d}",
            "reference": ch.challan_number if ch else "",
            "party": cust.name if cust else "",
            "date": p.payment_date.isoformat() if p.payment_date else None,
            "amount": float(p.amount or 0),
            "status": "paid",
            "method": p.payment_mode or "",
        })

    # Recent production
    q = db.query(ProductionEntry).filter(ProductionEntry.is_archived == False)  # noqa: E712
    if fdate:
        q = q.filter(ProductionEntry.created_at >= fdate)
    if tdate:
        q = q.filter(ProductionEntry.created_at <= tdate)
    productions = q.order_by(ProductionEntry.id.desc()).limit(limit).all()
    for prod in productions:
        product = prod.product
        activities.append({
            "type": "production",
            "id": prod.id,
            "title": f"Production {prod.production_number}",
            "reference": prod.production_number,
            "party": product.name if product else "",
            "date": prod.created_at.isoformat() if prod.created_at else None,
            "amount": 0,
            "status": "",
            "method": "",
        })

    # Sort by date desc
    activities.sort(key=lambda x: x["date"] or "", reverse=True)
    return activities[:limit]


# Sales trend
@router.get("/reports/sales-trend")
def sales_trend(
    period: str = "6m",
    db: Session = Depends(get_db),
):
    """Sales trend over a period.

    Returns the shape the Sales Overview dashboard card expects:
    {grand_total, invoice_count, avg_invoice, totals, expenses, material_cost, labels}.
    ``period`` is one of "7d", "30d", "6m", "1y" (default "6m").
    """
    now = _ist_wall()
    today = now.date()
    if period == "7d":
        start = today - timedelta(days=6)
    elif period == "30d":
        start = today - timedelta(days=29)
    elif period == "1y":
        start = date(today.year - 1, today.month, today.day)
    else:  # "6m" default
        # Six months back, inclusive of the current month.
        month = today.month - 5
        year = today.year
        while month <= 0:
            month += 12
            year -= 1
        start = date(year, month, 1)

    challans = (
        db.query(SalesChallan)
        .filter(
            SalesChallan.challan_date >= start,
            SalesChallan.challan_date <= today,
            SalesChallan.is_archived == False,  # noqa: E712
        )
        .order_by(SalesChallan.challan_date)
        .all()
    )

    # Bucket by calendar month within the period.
    buckets = {}
    for ch in challans:
        if not ch.challan_date:
            continue
        key = (ch.challan_date.year, ch.challan_date.month)
        b = buckets.setdefault(key, {"amount": 0.0, "count": 0})
        b["amount"] += float(ch.total_price or 0)
        b["count"] += 1

    # Material cost: consumption movements valued at rate, bucketed by month.
    mat_cost_by_month = {}
    q_mat = db.query(RawMaterialStockMovement).filter(
        RawMaterialStockMovement.movement_type == "consumption",
        RawMaterialStockMovement.rate.isnot(None),
    ).filter(
        RawMaterialStockMovement.created_at >= datetime(start.year, start.month, 1),
        RawMaterialStockMovement.created_at < datetime(today.year, today.month, today.day) + timedelta(days=1),
    )
    for mv in q_mat.all():
        key = (mv.created_at.year, mv.created_at.month)
        mat_cost_by_month[key] = mat_cost_by_month.get(key, 0.0) + float(
            (mv.quantity or 0) * (mv.rate or 0)
        )

    # Walk forward month by month from ``start`` to ``today`` so every
    # month in the window gets a label (zero where no sales).
    labels = []
    totals = []
    expenses = []
    material_cost = []
    cursor = date(start.year, start.month, 1)
    end = date(today.year, today.month, 1)
    while cursor <= end:
        key = (cursor.year, cursor.month)
        labels.append(cursor.strftime("%b %Y"))
        b = buckets.get(key, {"amount": 0.0, "count": 0})
        totals.append(round(b["amount"], 2))
        expenses.append(0.0)
        material_cost.append(round(mat_cost_by_month.get(key, 0.0), 2))
        # Advance one calendar month.
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)

    grand_total = round(sum(totals), 2)
    invoice_count = sum(b["count"] for b in buckets.values())
    avg_invoice = round(grand_total / invoice_count, 2) if invoice_count else 0.0

    return {
        "grand_total": grand_total,
        "invoice_count": invoice_count,
        "avg_invoice": avg_invoice,
        "totals": totals,
        "expenses": expenses,
        "material_cost": material_cost,
        "labels": labels,
    }


def analysis_monthly_sales(fy=None, year=None, db=None):
    """Monthly product sales, calendar (Jan-Dec) or FY (Apr-Mar) mode.

    Returns {months:[{key,label,amount}], grand_total_amount, fy, year}.
    fy=2026 => Apr 2026..Mar 2027; year=2026 => Jan..Dec 2026.
    Only confirmed, non-archived challans count.
    """
    from collections import defaultdict

    if fy is not None:
        try:
            fy = int(fy)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid fy")
        start = date(fy, 4, 1)
        end = date(fy + 1, 3, 31)
        out_year, out_fy = None, fy
    elif year is not None:
        try:
            year = int(year)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid year")
        start = date(year, 1, 1)
        end = date(year, 12, 31)
        out_year, out_fy = year, None
    else:
        now = _ist_wall().date()
        start = date(now.year, 1, 1)
        end = date(now.year, 12, 31)
        out_year, out_fy = now.year, None

    rows = (
        db.query(SalesChallan)
        .filter(
            SalesChallan.status == "confirmed",
            SalesChallan.is_archived == False,  # noqa: E712
            SalesChallan.challan_date >= start,
            SalesChallan.challan_date <= end,
        )
        .all()
    )

    buckets = defaultdict(float)
    for ch in rows:
        cd = ch.challan_date
        if cd is None:
            continue
        buckets[(cd.year, cd.month)] += float(ch.total_price or 0)

    months = []
    grand_total = 0.0
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        key = (cursor.year, cursor.month)
        amount = round(buckets.get(key, 0.0), 2)
        months.append({"key": cursor.strftime("%Y-%m"), "label": cursor.strftime("%b"), "amount": amount})
        grand_total += amount
        cursor = date(cursor.year + 1, 1, 1) if cursor.month == 12 else date(cursor.year, cursor.month + 1, 1)

    return {
        "months": months,
        "grand_total_amount": round(grand_total, 2),
        "fy": out_fy,
        "year": out_year,
    }


def analysis_monthly_export(fy=None, year=None, db=None):
    """CSV export of monthly sales, honouring fy/year like analysis_monthly_sales."""
    import csv
    import io

    from fastapi.responses import StreamingResponse

    data = analysis_monthly_sales(fy=fy, year=year, db=db)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["month", "label", "amount"])
    for row in data["months"]:
        writer.writerow([row["key"], row["label"], row["amount"]])
    label = f"FY{fy}" if fy is not None else f"Year{year or ''}"
    resp = StreamingResponse(iter([output.getvalue()]), media_type="text/csv")
    resp.headers["Content-Disposition"] = f'attachment; filename="{label}.csv"'
    return resp


# Custom reports
@router.get("/reports/custom/fields")
def custom_report_fields():
    """Available fields for custom report builder."""
    return {
        "sales_challan": ["challan_number", "date", "customer", "total_amount", "status"],
        "purchase_invoice": ["invoice_number", "date", "supplier", "total_amount", "transport_charges"],
        "production_entry": ["production_number", "created_at", "product", "status"],
        "customer": ["name", "phone", "address", "gstin"],
        "supplier": ["name", "phone", "address", "gstin"],
        "product": ["name", "category", "default_rate", "gst_rate"],
        "raw_material": ["name", "unit", "current_stock", "threshold_stock"],
        "employee": ["name", "role", "phone", "basic_salary"],
    }


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _custom_report_rows(src, wanted, entity_field, entity_value, fdate, tdate, db):
    """Build (columns, rows, amount_total) for a custom-report source."""
    full_columns = []
    rows = []

    if src == "sales":
        full_columns = [
            "date", "challan", "customer", "product", "packaging",
            "quantity", "weight_kg", "amount",
        ]
        q = (
            db.query(SalesChallan, SalesChallanItem)
            .join(SalesChallanItem, SalesChallanItem.challan_id == SalesChallan.id)
            .filter(SalesChallan.is_archived == False)  # noqa: E712
        )
        if fdate:
            q = q.filter(SalesChallan.challan_date >= fdate)
        if tdate:
            q = q.filter(SalesChallan.challan_date <= tdate)
        if entity_field == "product" and entity_value:
            q = q.join(Product, Product.id == SalesChallanItem.product_id).filter(
                Product.name == entity_value
            )
        for ch, it in q.all():
            rows.append({
                "date": ch.challan_date.isoformat() if ch.challan_date else None,
                "challan": ch.challan_number,
                "customer": ch.customer.name if ch.customer else None,
                "product": it.product.name if it.product else None,
                "packaging": it.packaging_size.size_name if it.packaging_size else None,
                "quantity": float(it.quantity or 0),
                "weight_kg": float(it.weight or 0),
                "amount": float(it.amount or 0),
            })

    elif src == "dues":
        full_columns = ["customer", "company", "contact", "outstanding"]
        balances = _snap.all_challan_balances(db)
        challans = (
            db.query(SalesChallan)
            .filter(
                SalesChallan.is_archived == False,  # noqa: E712
                SalesChallan.status != "DRAFT",
            )
            .all()
        )
        by_customer = {}
        for ch in challans:
            bal = balances.get(ch.id, 0)
            if bal <= 0:
                continue
            c = ch.customer
            row = by_customer.setdefault(
                c.id,
                {"customer": c.name or "", "company": c.company_name or "",
                 "contact": c.contact_number or "", "outstanding": 0.0},
            )
            row["outstanding"] += float(bal)
        if entity_field == "customer" and entity_value:
            by_customer = {
                k: v for k, v in by_customer.items() if v["customer"] == entity_value
            }
        rows = list(by_customer.values())

    elif src == "purchases":
        full_columns = [
            "date", "invoice", "supplier", "material", "quantity",
            "amount", "paid", "outstanding",
        ]
        q = (
            db.query(PurchaseInvoice, PurchaseInvoiceItem)
            .join(
                PurchaseInvoiceItem,
                PurchaseInvoiceItem.purchase_invoice_id == PurchaseInvoice.id,
            )
            .filter(PurchaseInvoice.is_archived == False)  # noqa: E712
        )
        if fdate:
            q = q.filter(PurchaseInvoice.invoice_date >= fdate)
        if tdate:
            q = q.filter(PurchaseInvoice.invoice_date <= tdate)
        if entity_field == "supplier" and entity_value:
            q = q.join(Supplier, Supplier.id == PurchaseInvoice.supplier_id).filter(
                Supplier.name == entity_value
            )
        for inv, it in q.all():
            rows.append({
                "date": inv.invoice_date.isoformat() if inv.invoice_date else None,
                "invoice": inv.invoice_number,
                "supplier": inv.supplier.name if inv.supplier else None,
                "material": it.raw_material.name if it.raw_material else None,
                "quantity": float(it.quantity or 0),
                "amount": float(it.amount or 0),
                "paid": float(inv.paid_amount or 0),
                "outstanding": float(inv.outstanding_amount or 0),
            })

    elif src == "expenses":
        full_columns = ["date", "category", "description", "amount", "payee"]
        q = db.query(Expense).filter(Expense.is_archived == False)  # noqa: E712
        if fdate:
            q = q.filter(Expense.date >= fdate)
        if tdate:
            q = q.filter(Expense.date <= tdate)
        if entity_field == "category" and entity_value:
            q = q.join(ExpenseCategory, ExpenseCategory.id == Expense.category_id).filter(
                ExpenseCategory.name == entity_value
            )
        for e in q.order_by(Expense.date.desc()).all():
            rows.append({
                "date": e.date.isoformat() if e.date else None,
                "category": e.category.name if e.category else None,
                "description": e.description or "",
                "amount": float(e.amount or 0),
                "payee": e.payee or "",
            })

    elif src == "production":
        full_columns = [
            "date", "number", "product", "packaging",
            "target_kg", "actual_kg", "status",
        ]
        q = (
            db.query(ProductionEntry, PackagingSize)
            .outerjoin(PackagingSize, PackagingSize.id == ProductionEntry.packaging_size_id)
            .filter(ProductionEntry.is_archived == False)  # noqa: E712
        )
        if fdate:
            q = q.filter(ProductionEntry.production_date >= fdate)
        if tdate:
            q = q.filter(ProductionEntry.production_date <= tdate)
        if entity_field == "product" and entity_value:
            q = q.join(Product, Product.id == ProductionEntry.product_id).filter(
                Product.name == entity_value
            )
        for pe, ps in q.order_by(ProductionEntry.production_date.desc(), ProductionEntry.id.desc()).all():
            rows.append({
                "date": pe.production_date.isoformat() if pe.production_date else None,
                "number": pe.production_number,
                "product": pe.product.name if pe.product else None,
                "packaging": ps.size_name if ps else None,
                "target_kg": float(pe.target_quantity or 0),
                "actual_kg": float(pe.actual_output or 0),
                "status": pe.status.value if pe.status else None,
            })

    columns = wanted or full_columns
    rows = [{c: r.get(c) for c in columns} for r in rows]
    amount_total = 0.0
    if "amount" in columns:
        amount_total = round(sum(float(r.get("amount") or 0) for r in rows), 2)
    return columns, rows, amount_total


@router.get("/reports/custom")
def run_custom_report(
    source: str | None = None,
    report_type: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    columns: str | None = None,
    fields: str | None = None,
    entity_field: str | None = None,
    entity_value: str | None = None,
    db: Session = Depends(get_db),
):
    """Run a custom report.

    Accepts ``source`` (preferred) or legacy ``report_type``; ``columns``
    (preferred) or legacy ``fields`` (comma-separated). Optional entity
    filter via ``entity_field``/``entity_value``. Returns
    {rows, columns, count, amount_total?}.
    """
    src = (source or report_type or "").strip()
    if not src:
        return {"rows": [], "columns": [], "count": 0, "amount_total": 0.0}

    fdate = _parse_date(from_date)
    tdate = _parse_date(to_date)
    raw_cols = (columns or fields or "").strip()
    wanted = [c.strip() for c in raw_cols.split(",") if c.strip()] if raw_cols else None
    columns, rows, amount_total = _custom_report_rows(
        src, wanted, entity_field, entity_value, fdate, tdate, db
    )
    return {"rows": rows, "columns": columns, "count": len(rows), "amount_total": amount_total}


@router.get("/reports/custom/export")
def export_custom_report(
    source: str | None = None,
    report_type: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    columns: str | None = None,
    fields: str | None = None,
    entity_field: str | None = None,
    entity_value: str | None = None,
    format: str | None = None,
    db: Session = Depends(get_db),
):
    """Export custom report as CSV."""
    src = (source or report_type or "").strip()
    if not src:
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse("", media_type="text/csv")

    fdate = _parse_date(from_date)
    tdate = _parse_date(to_date)
    raw_cols = (columns or fields or "").strip()
    wanted = [c.strip() for c in raw_cols.split(",") if c.strip()] if raw_cols else None
    report_columns, rows, _amount_total = _custom_report_rows(
        src, wanted, entity_field, entity_value, fdate, tdate, db
    )
    from fastapi.responses import PlainTextResponse
    import csv
    import io
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=report_columns)
    writer.writeheader()
    writer.writerows(rows)
    return PlainTextResponse(output.getvalue(), media_type="text/csv")


# Saved reports
@router.get("/reports/saved", response_model=list[SavedReportSchema])
def list_saved_reports(db: Session = Depends(get_db)):
    return db.query(SavedReport).order_by(SavedReport.id.desc()).all()


@router.post("/reports/saved", response_model=SavedReportSchema)
def create_saved_report(payload: dict, request: Request, db: Session = Depends(get_db)):
    name = (payload or {}).get("name")
    report_type = (payload or {}).get("report_type")
    config = (payload or {}).get("config")
    if not name or not report_type or not config:
        raise HTTPException(status_code=400, detail="name, report_type, config required")
    report = SavedReport(name=name, report_type=report_type, config_json=config, created_by=_current_user(db, request).id if _current_user(db, request) else None)
    db.add(report)
    _audit(db, request, "reports", "saved_create", record=report, record_type="saved_report", new_values={"name": name, "type": report_type})
    db.commit()
    db.refresh(report)
    return report


@router.put("/reports/saved/{report_id}", response_model=SavedReportSchema)
def update_saved_report(report_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    report = db.query(SavedReport).filter(SavedReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Saved report not found")
    if "name" in payload:
        report.name = payload["name"]
    if "config" in payload:
        report.config_json = payload["config"]
    _audit(db, request, "reports", "saved_update", record=report, record_type="saved_report", new_values={"name": report.name})
    db.commit()
    db.refresh(report)
    return report


@router.delete("/reports/saved/{report_id}")
def delete_saved_report(report_id: int, request: Request, db: Session = Depends(get_db)):
    report = db.query(SavedReport).filter(SavedReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Saved report not found")
    _audit(db, request, "reports", "saved_delete", record=report, record_type="saved_report", old_values={"name": report.name})
    db.delete(report)
    db.commit()
    return {"status": "deleted", "id": report_id}


# --- Expenses (CRUD + archive/restore) ---


@router.get("/expenses")
def list_expenses(
    include_archived: bool = False,
    db: Session = Depends(get_db),
):
    q = db.query(Expense)
    if not include_archived:
        q = q.filter(Expense.is_archived == False)  # noqa: E712
    expenses = q.order_by(Expense.date.desc()).all()
    return [
        {
            "id": e.id,
            "date": e.date.isoformat() if e.date else None,
            "created_at": e.created_at,
            "description": e.description,
            "category_name": e.category if e.category else "",
            "category_id": e.category_id,
            "payee": e.payee,
            "payment_mode": e.payment_mode,
            "amount": float(e.amount or 0),
            "notes": e.notes,
        }
        for e in expenses
    ]


@router.get("/expense-categories")
def list_expense_categories(db: Session = Depends(get_db)):
    cats = (
        db.query(ExpenseCategory)
        .filter(ExpenseCategory.is_active == True)  # noqa: E712
        .order_by(ExpenseCategory.name)
        .all()
    )
    return [{"id": c.id, "name": c.name} for c in cats]


@router.get("/expenses/history")
def expense_history(db: Session = Depends(get_db)):
    payees = (
        db.query(Expense.payee)
        .distinct()
        .filter(Expense.payee.isnot(None))
        .all()
    )
    descriptions = (
        db.query(Expense.description)
        .distinct()
        .filter(Expense.description.isnot(None))
        .all()
    )
    return {
        "payees": [p[0] for p in payees],
        "descriptions": [d[0] for d in descriptions],
    }


@router.post("/expenses")
def create_expense(payload: dict, request: Request, db: Session = Depends(get_db)):
    data = payload or {}
    category_id = data.get("category_id")
    category_name = (data.get("category_name") or "").strip() or None
    date_str = data.get("date")
    description = data.get("description") or ""
    amount = data.get("amount") or 0
    payment_mode = data.get("payment_mode") or "cash"
    payee = data.get("payee") or ""
    notes = data.get("notes")

    # If a new category name was given, create it; else use the picked id.
    if category_name:
        existing = (
            db.query(ExpenseCategory)
            .filter(ExpenseCategory.name == category_name)
            .first()
        )
        if existing:
            category_id = existing.id
        else:
            cat = ExpenseCategory(name=category_name)
            db.add(cat)
            db.flush()
            category_id = cat.id

    if not date_str:
        raise HTTPException(status_code=400, detail="date required")
    exp_date = datetime.fromisoformat(date_str).date()

    exp = Expense(
        category_id=category_id,
        description=description,
        amount=amount,
        date=exp_date,
        payment_mode=payment_mode,
        payee=payee,
        notes=notes,
    )
    db.add(exp)
    _audit(db, request, "expenses", "create", record=exp, record_type="expense",
           new_values={"description": description, "amount": amount})
    db.commit()
    db.refresh(exp)
    cat_name = exp.category if exp.category else "Miscellaneous"
    return {"category_name": cat_name, "id": exp.id, "created_at": exp.created_at}


@router.delete("/expenses/{expense_id}")
def archive_expense(expense_id: int, request: Request, db: Session = Depends(get_db)):
    exp = db.query(Expense).filter(Expense.id == expense_id).first()
    if not exp:
        raise HTTPException(status_code=404, detail="Expense not found")
    reason = _change_reason(request)
    if not _archive_record(db, request, exp, "expenses", "expense"):
        raise HTTPException(status_code=409, detail="Expense already archived")
    _audit(db, request, "expenses", "archive", record=exp, record_type="expense",
           old_values={"is_archived": False}, new_values={"is_archived": True}, notes=reason)
    db.commit()
    return {"status": "archived", "id": expense_id}


@router.post("/expenses/{expense_id}/restore")
def restore_expense(expense_id: int, request: Request, db: Session = Depends(get_db)):
    exp = db.query(Expense).filter(Expense.id == expense_id).first()
    if not exp:
        raise HTTPException(status_code=404, detail="Expense not found")
    reason = _change_reason(request)
    if not _restore_record(db, request, exp, "expenses", "expense"):
        raise HTTPException(status_code=409, detail="Expense is not archived")
    _audit(db, request, "expenses", "restore", record=exp, record_type="expense",
           old_values={"is_archived": True}, new_values={"is_archived": False}, notes=reason)
    db.commit()
    return {"status": "restored", "id": expense_id}


# --- Dues follow-up ---


@router.get("/reports/dues-followup")
def dues_followup(db: Session = Depends(get_db)):
    """One row per customer with unpaid challans, oldest due first."""
    balances = _snap.all_challan_balances(db)

    # Non-archived, non-draft challans with a positive outstanding balance.
    challans = (
        db.query(SalesChallan)
        .filter(
            SalesChallan.is_archived == False,  # noqa: E712
            SalesChallan.status != "DRAFT",
        )
        .all()
    )

    by_customer = {}
    for ch in challans:
        bal = balances.get(ch.id, 0)
        if bal <= 0:
            continue
        cid = ch.customer_id
        if cid not in by_customer:
            by_customer[cid] = {
                "customer_name": ch.customer.name if ch.customer else "—",
                "contact_number": ch.customer.contact_number if ch.customer else None,
                "contact_number_2": None,
                "total_outstanding": 0.0,
                "oldest_challan_date": None,
                "challan_count": 0,
            }
        row = by_customer[cid]
        row["total_outstanding"] += float(bal)
        row["challan_count"] += 1
        cd = ch.challan_date
        if cd and (row["oldest_challan_date"] is None or cd < row["oldest_challan_date"]):
            row["oldest_challan_date"] = cd

    now = _ist_wall()
    result = []
    for row in by_customer.values():
        oldest = row["oldest_challan_date"]
        if oldest:
            row["max_age_days"] = (now.date() - oldest.date()).days
        else:
            row["max_age_days"] = 0
        row["oldest_challan_date"] = oldest.isoformat() if oldest else None
        result.append(row)

    result.sort(key=lambda r: r["max_age_days"], reverse=True)
    return result


@router.get("/expenses/history")
def expense_history(db: Session = Depends(get_db)):
    """Distinct expense payees and descriptions for datalist pickers."""
    return expense_history_options(db)


# Daily report sections for the morning mail / scheduled report.
REPORT_SECTION_CHOICES = ["dues", "stock", "production", "purchases", "expenses"]


def _build_daily_report(db, sections=None):
    """Build a daily report email: (subject, plain-text body, HTML body).

    Only the sections listed in `sections` are included. Each section's
    rows are rendered once; a section with no rows says so explicitly.
    When ``sections`` is omitted every known section is included.
    """
    if sections is None or len(sections) == 0:
        sections = list(REPORT_SECTION_CHOICES)
    sections = set(sections)
    subject = "Daily Report — Shresth Masala Industry"
    lines = [f"{subject}", ""]
    html = [f"<h2>{subject}</h2>"]

    if "dues" in sections:
        dues_rows = dues_followup(db)
        if dues_rows:
            lines.append("Dues follow-up (oldest due first):")
            html.append("<h3>Dues follow-up</h3><ul>")
            for d in dues_rows:
                name = d["customer_name"]
                amount = d["total_outstanding"]
                days = d["max_age_days"]
                lines.append(f"  {name}: ₹{amount:.2f} — {days} days waiting")
                html.append(f"<li>{name}: ₹{amount:.2f} — {days} days waiting</li>")
            html.append("</ul>")
        else:
            lines.append("Dues follow-up: nothing overdue.")
            html.append("<p>Dues follow-up: nothing overdue.</p>")

    if len(lines) <= 2:
        lines.append("Nothing to report.")
        html.append("<p>Nothing to report.</p>")

    if "low_stock" in sections:
        low = reports_summary(None, None, db).get("low_stock", [])
        if low:
            lines.append("Low stock watch:")
            html.append("<h3>Low stock watch</h3><ul>")
            for d in low:
                lines.append(f"  {d['name']}: {d['current_stock']}kg (threshold {d['threshold_stock']}kg)")
                html.append(f"<li>{d['name']}: {d['current_stock']}kg (threshold {d['threshold_stock']}kg)</li>")
            html.append("</ul>")
        else:
            lines.append("Low stock watch: none.")
            html.append("<p>Low stock watch: none.</p>")

    if len(lines) <= 2:
        lines.append("Nothing to report.")
        html.append("<p>Nothing to report.</p>")

    text = "\n".join(lines)
    html_body = "\n".join(html)
    return subject, text, html_body


def expense_history_options(db: Session):
    """Distinct expense payees and descriptions for datalist pickers.

    Most-recent-first, case-insensitive dedup (latest casing wins).
    Returns {"payees": [...], "descriptions": [...]}.
    """
    rows = (
        db.query(Expense)
        .order_by(Expense.date.desc(), Expense.id.desc())
        .all()
    )
    payees, descriptions = [], []
    seen_p, seen_d = set(), set()
    for e in rows:
        payee = (e.payee or "").strip()
        if payee:
            key = payee.casefold()
            if key not in seen_p:
                seen_p.add(key)
                payees.append(payee)
        desc = (e.description or "").strip()
        if desc:
            key = desc.casefold()
            if key not in seen_d:
                seen_d.add(key)
                descriptions.append(desc)
    return {"payees": payees, "descriptions": descriptions}