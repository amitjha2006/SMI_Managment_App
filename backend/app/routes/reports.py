"""Report routes: raw material summary, purchase summary, payment summary, production history, profit-loss, summary, recent, sales trend, custom reports, saved reports"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session
from datetime import datetime, date, timedelta
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
    from_date: str | None = None,
    to_date: str | None = None,
    db: Session = Depends(get_db),
):
    """Raw material consumption vs purchase summary.

    Returns {rows, from, to} so the frontend can render per-material
    stock/production/balance/wastage figures. Fields match what the
    frontend expects: name, stock_kg, production_kg, balance_kg,
    wastage_kg, wastage_pct.
    """
    q_consumption = db.query(
        RawMaterialStockMovement.raw_material_id,
        func.sum(RawMaterialStockMovement.quantity).label("consumed_qty"),
    ).filter(RawMaterialStockMovement.movement_type == "consumption")

    if from_date:
        dt = datetime.fromisoformat(from_date)
        q_consumption = q_consumption.filter(RawMaterialStockMovement.created_at >= dt)
    if to_date:
        dt = datetime.fromisoformat(to_date)
        q_consumption = q_consumption.filter(RawMaterialStockMovement.created_at <= dt + timedelta(days=1))

    consumption = {
        r.raw_material_id: float(r.consumed_qty or 0)
        for r in q_consumption.group_by(RawMaterialStockMovement.raw_material_id).all()
    }

    materials = db.query(RawMaterial).filter(RawMaterial.is_active == True).all()  # noqa: E712
    rows = []
    for m in materials:
        stock = float(m.current_stock or 0)
        production = consumption.get(m.id, 0)
        balance = stock - production
        rows.append({
            "name": m.name,
            "stock_kg": stock,
            "production_kg": production,
            "balance_kg": balance,
            "wastage_kg": 0.0,
            "wastage_pct": 0.0,
        })
    return {"rows": rows, "from": from_date or "", "to": to_date or ""}


# Purchase summary
@router.get("/reports/purchase-summary")
def purchase_summary(
    from_date: str | None = None,
    to_date: str | None = None,
    supplier_id: int | None = None,
    db: Session = Depends(get_db),
):
    """Purchase invoice summary.

    Returns {rows, from, to} where each row is a raw material with
    purchased_kg and purchase_amount aggregated across confirmed invoices.
    Fields match what the frontend expects: name, purchased_kg,
    purchase_amount.
    """
    q = db.query(PurchaseInvoice).filter(PurchaseInvoice.is_archived == False)  # noqa: E712
    if from_date:
        q = q.filter(PurchaseInvoice.invoice_date >= datetime.fromisoformat(from_date).date())
    if to_date:
        q = q.filter(PurchaseInvoice.invoice_date <= datetime.fromisoformat(to_date).date())
    if supplier_id:
        q = q.filter(PurchaseInvoice.supplier_id == supplier_id)

    invoices = q.all()

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

    rows = [
        {
            "name": v["name"],
            "purchased_kg": v["qty"],
            "purchase_amount": v["amount"],
        }
        for v in agg.values()
    ]
    rows.sort(key=lambda r: r["name"] or "")
    return {"rows": rows, "from": from_date or "", "to": to_date or ""}


# Payment summary
@router.get("/reports/payment-summary")
def payment_summary(
    from_date: str | None = None,
    to_date: str | None = None,
    db: Session = Depends(get_db),
):
    """Customer payment summary.

    Returns {receipts, from, to} where each receipt matches the fields the
    frontend reads: customer_name, challan_number, payment_mode,
    transaction_ref, amount, payment_date, created_at, challan_id, id.
    """
    q = db.query(CustomerPayment)
    if from_date:
        q = q.filter(CustomerPayment.payment_date >= datetime.fromisoformat(from_date).date())
    if to_date:
        q = q.filter(CustomerPayment.payment_date <= datetime.fromisoformat(to_date).date())

    payments = q.order_by(CustomerPayment.payment_date.desc()).all()
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
    return {"receipts": receipts, "from": from_date or "", "to": to_date or ""}


# Production history
@router.get("/reports/production-history")
def production_history(
    from_date: str | None = None,
    to_date: str | None = None,
    product_id: int | None = None,
    db: Session = Depends(get_db),
):
    """Production entry history."""
    q = db.query(ProductionEntry).filter(ProductionEntry.is_archived == False)  # noqa: E712
    if from_date:
        q = q.filter(ProductionEntry.created_at >= datetime.fromisoformat(from_date))
    if to_date:
        q = q.filter(ProductionEntry.created_at <= datetime.fromisoformat(to_date) + timedelta(days=1))
    if product_id:
        q = q.filter(ProductionEntry.product_id == product_id)

    entries = q.order_by(ProductionEntry.created_at.desc()).all()
    return [
        {
            "id": e.id,
            "production_number": e.production_number,
            "product_id": e.product_id,
            "product_name": e.product.name if e.product else None,
            "status": e.status.value if e.status else None,
            "created_at": e.created_at.isoformat() if e.created_at else None,
            "completed_at": e.completed_at.isoformat() if e.completed_at else None,
            "outputs": [
                {
                    "packaging_size_id": o.packaging_size_id,
                    "size_name": o.packaging.size_name if o.packaging else None,
                    "packets": o.packets,
                    "weight_kg": float(o.weight_kg or 0),
                }
                for o in e.outputs
            ],
        }
        for e in entries
    ]


# Profit & Loss
@router.get("/reports/profit-loss")
def profit_loss(
    from_date: str | None = None,
    to_date: str | None = None,
    db: Session = Depends(get_db),
):
    """Profit & Loss report."""
    # Revenue: confirmed sales challans
    q_sales = db.query(SalesChallan).filter(
        SalesChallan.status == "confirmed",
        SalesChallan.is_archived == False,  # noqa: E712
    )
    if from_date:
        q_sales = q_sales.filter(SalesChallan.challan_date >= datetime.fromisoformat(from_date).date())
    if to_date:
        q_sales = q_sales.filter(SalesChallan.challan_date <= datetime.fromisoformat(to_date).date())
    revenue = sum(float(ch.total_price or 0) for ch in q_sales.all())

    # COGS: raw material consumption valued at purchase rate
    # Simplified: use purchase invoices in period
    q_purchases = db.query(PurchaseInvoice).filter(PurchaseInvoice.is_archived == False)  # noqa: E712
    if from_date:
        q_purchases = q_purchases.filter(PurchaseInvoice.invoice_date >= datetime.fromisoformat(from_date).date())
    if to_date:
        q_purchases = q_purchases.filter(PurchaseInvoice.invoice_date <= datetime.fromisoformat(to_date).date())
    cogs = sum(float(inv.total_amount or 0) for inv in q_purchases.all())

    # Expenses
    q_expenses = db.query(Expense).filter(Expense.is_archived == False)  # noqa: E712
    if from_date:
        q_expenses = q_expenses.filter(Expense.date >= datetime.fromisoformat(from_date).date())
    if to_date:
        q_expenses = q_expenses.filter(Expense.date <= datetime.fromisoformat(to_date).date())
    expenses = sum(float(e.amount or 0) for e in q_expenses.all())

    # Payroll
    q_payroll = db.query(SalaryPayment).filter(SalaryPayment.is_archived == False)  # noqa: E712
    if from_date:
        q_payroll = q_payroll.filter(SalaryPayment.payment_date >= datetime.fromisoformat(from_date).date())
    if to_date:
        q_payroll = q_payroll.filter(SalaryPayment.payment_date <= datetime.fromisoformat(to_date).date())
    payroll = sum(float(p.net_pay or 0) for p in q_payroll.all())

    gross_profit = revenue - cogs
    net_profit = gross_profit - expenses - payroll

    # Period as a display string (frontend concatenates it into a label).
    if from_date and to_date:
        period_str = f"{from_date} to {to_date}"
    elif from_date:
        period_str = f"from {from_date}"
    elif to_date:
        period_str = f"to {to_date}"
    else:
        period_str = "All time"

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
    if from_date:
        fdate = datetime.fromisoformat(from_date).date()
    if to_date:
        tdate = datetime.fromisoformat(to_date).date()

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
    if tdate:
        q = q.filter(PurchaseInvoice.invoice_date <= tdate)
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
        material_cost.append(0.0)
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


@router.get("/reports/custom")
def run_custom_report(
    report_type: str,
    from_date: str | None = None,
    to_date: str | None = None,
    fields: str | None = None,
    db: Session = Depends(get_db),
):
    """Run a custom report."""
    if report_type == "sales_challan":
        q = db.query(SalesChallan).filter(SalesChallan.is_archived == False)  # noqa: E712
        if from_date:
            q = q.filter(SalesChallan.challan_date >= datetime.fromisoformat(from_date).date())
        if to_date:
            q = q.filter(SalesChallan.challan_date <= datetime.fromisoformat(to_date).date())
        rows = q.all()
        return [
            {
                "challan_number": r.challan_number,
                "date": r.date.isoformat() if r.date else None,
                "customer": r.customer.name if r.customer else None,
                "total_amount": float(r.total_price or 0),
                "status": r.status.value if r.status else None,
            }
            for r in rows
        ]

    # Add other report types as needed
    return []


@router.get("/reports/custom/export")
def export_custom_report(
    report_type: str,
    from_date: str | None = None,
    to_date: str | None = None,
    db: Session = Depends(get_db),
):
    """Export custom report as CSV."""
    data = run_custom_report(report_type, from_date, to_date, None, db)
    from fastapi.responses import PlainTextResponse
    import csv
    import io
    output = io.StringIO()
    if data:
        writer = csv.DictWriter(output, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)
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
            "category_name": e.category.name if e.category else "",
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
    cat_name = exp.category.name if exp.category else "Miscellaneous"
    return {"category_name": cat_name, "id": exp.id, "created_at": exp.created_at}


@router.delete("/expenses/{expense_id}")
def archive_expense(expense_id: int, request: Request, db: Session = Depends(get_db)):
    exp = db.query(Expense).filter(Expense.id == expense_id).first()
    if not exp:
        raise HTTPException(status_code=404, detail="Expense not found")
    if not _archive_record(db, request, exp, "expenses", "expense"):
        raise HTTPException(status_code=409, detail="Expense already archived")
    _audit(db, request, "expenses", "archive", record=exp, record_type="expense",
           old_values={"is_archived": False}, new_values={"is_archived": True})
    db.commit()
    return {"status": "archived", "id": expense_id}


@router.post("/expenses/{expense_id}/restore")
def restore_expense(expense_id: int, request: Request, db: Session = Depends(get_db)):
    exp = db.query(Expense).filter(Expense.id == expense_id).first()
    if not exp:
        raise HTTPException(status_code=404, detail="Expense not found")
    if not _restore_record(db, request, exp, "expenses", "expense"):
        raise HTTPException(status_code=409, detail="Expense is not archived")
    _audit(db, request, "expenses", "restore", record=exp, record_type="expense",
           old_values={"is_archived": True}, new_values={"is_archived": False})
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


def _build_daily_report(db, sections):
    """Build a daily report email: (subject, plain-text body, HTML body).

    Only the sections listed in `sections` are included. Each section's
    rows are rendered once; a section with no rows says so explicitly.
    """
    sections = set(sections or [])
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