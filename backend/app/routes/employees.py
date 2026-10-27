"""Employee, attendance, payroll, advances, deductions routes"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session
from datetime import date, datetime, timedelta
from decimal import Decimal

from backend.app.core.database import get_db
from backend.app.models import (
    Attendance,
    Deduction,
    Employee,
    EmployeeAdvance,
    Leave,
    SalaryPayment,
)
from backend.app.models.employee import EmployeeStatus
from backend.app.schemas import EmployeeSchema
from backend.app.core.shared import (
    _audit,
    _change_reason,
    _reject_if_archived,
    _archive_record,
    _restore_record,
    _ist_wall,
    _request_user_and_role,
)
from backend.app.services import payroll as _pay

router = APIRouter()


# Employee routes
@router.get("/employees", response_model=list[EmployeeSchema])
def list_employees(db: Session = Depends(get_db)):
    return db.query(Employee).order_by(Employee.name).all()


@router.post("/employees", response_model=EmployeeSchema)
def create_employee(employee: EmployeeSchema, request: Request, db: Session = Depends(get_db)):
    if not employee.name:
        raise HTTPException(status_code=400, detail="name required")
    if db.query(Employee).filter(Employee.name == employee.name).first():
        raise HTTPException(status_code=400, detail="Employee with this name already exists")
    e = Employee(
        name=employee.name,
        role=employee.role,
        phone=employee.phone,
        address=employee.address,
        aadhaar=employee.aadhaar,
        pan=employee.pan,
        bank_account=employee.bank_account,
        ifsc=employee.ifsc,
        basic_salary=employee.basic_salary or 0,
        hra=employee.hra or 0,
        allowance=employee.allowance or 0,
        deduction=employee.deduction or 0,
        joining_date=employee.joining_date,
        is_active=True,
    )
    db.add(e)
    _audit(db, request, "employees", "create", record=e, record_type="employee", new_values={"name": e.name, "role": str(e.role)})
    db.commit()
    db.refresh(e)
    return e


@router.get("/employees/{employee_id}", response_model=EmployeeSchema)
def get_employee(employee_id: int, db: Session = Depends(get_db)):
    e = db.query(Employee).filter(Employee.id == employee_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Employee not found")
    return e


@router.put("/employees/{employee_id}", response_model=EmployeeSchema)
def update_employee(employee_id: int, employee: EmployeeSchema, request: Request, db: Session = Depends(get_db)):
    e = db.query(Employee).filter(Employee.id == employee_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Employee not found")
    _reject_if_archived(e, "employee")
    reason = _change_reason(request)
    data = employee.dict(exclude_unset=True)
    for field, value in data.items():
        if value is not None:
            setattr(e, field, value)
    _audit(db, request, "employees", "update", record=e, record_type="employee", new_values={"name": e.name, "role": str(e.role)}, notes=reason)
    db.commit()
    db.refresh(e)
    return e


@router.delete("/employees/{employee_id}")
def archive_employee(employee_id: int, request: Request, db: Session = Depends(get_db)):
    e = db.query(Employee).filter(Employee.id == employee_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Employee not found")
    reason = _change_reason(request)
    if not _archive_record(db, request, e, "employees", "employee"):
        raise HTTPException(status_code=409, detail="Employee already archived")
    _audit(db, request, "employees", "archive", record=e, record_type="employee", old_values={"is_active": True, "is_archived": False}, new_values={"is_active": False, "is_archived": True}, notes=reason)
    db.commit()
    return {"status": "archived", "id": employee_id}


@router.post("/employees/{employee_id}/restore")
def restore_employee(employee_id: int, request: Request, db: Session = Depends(get_db)):
    e = db.query(Employee).filter(Employee.id == employee_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Employee not found")
    reason = _change_reason(request)
    if not _restore_record(db, request, e, "employees", "employee"):
        raise HTTPException(status_code=409, detail="Employee is not archived")
    _audit(db, request, "employees", "restore", record=e, record_type="employee", old_values={"is_archived": True}, new_values={"is_archived": False}, notes=reason)
    db.commit()
    return {"status": "restored", "id": employee_id}


# Attendance
@router.get("/attendance")
def list_attendance(
    employee_id: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    db: Session = Depends(get_db),
):
    from datetime import datetime
    q = db.query(Attendance)
    if employee_id:
        q = q.filter(Attendance.employee_id == employee_id)
    if from_date:
        q = q.filter(Attendance.date >= datetime.fromisoformat(from_date).date())
    if to_date:
        q = q.filter(Attendance.date <= datetime.fromisoformat(to_date).date())
    return q.order_by(Attendance.date.desc()).all()


@router.post("/attendance")
def record_attendance(payload: dict, request: Request, db: Session = Depends(get_db)):
    employee_id = (payload or {}).get("employee_id")
    date_str = (payload or {}).get("date")
    status = (payload or {}).get("status")
    if not employee_id or not date_str or not status:
        raise HTTPException(status_code=400, detail="employee_id, date, status required")
    from datetime import datetime
    att = Attendance(
        employee_id=employee_id,
        date=datetime.fromisoformat(date_str).date(),
        status=status,
    )
    db.add(att)
    _audit(db, request, "employees", "attendance_create", record=att, record_type="attendance", new_values={"employee_id": employee_id, "date": date_str, "status": status})
    db.commit()
    return {"status": "recorded", "id": att.id}


# Leaves
@router.get("/leaves")
def list_leaves(
    employee_id: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    db: Session = Depends(get_db),
):
    from datetime import datetime
    q = db.query(Leave)
    if employee_id:
        q = q.filter(Leave.employee_id == employee_id)
    if from_date:
        q = q.filter(Leave.start_date >= datetime.fromisoformat(from_date).date())
    if to_date:
        q = q.filter(Leave.end_date <= datetime.fromisoformat(to_date).date())
    return q.order_by(Leave.start_date.desc()).all()


@router.post("/leaves")
def record_leave(payload: dict, request: Request, db: Session = Depends(get_db)):
    employee_id = (payload or {}).get("employee_id")
    start_date = (payload or {}).get("start_date")
    end_date = (payload or {}).get("end_date")
    leave_type = (payload or {}).get("leave_type")
    if not employee_id or not start_date or not end_date or not leave_type:
        raise HTTPException(status_code=400, detail="employee_id, start_date, end_date, leave_type required")
    from datetime import datetime
    leave = Leave(
        employee_id=employee_id,
        start_date=datetime.fromisoformat(start_date).date(),
        end_date=datetime.fromisoformat(end_date).date(),
        leave_type=leave_type,
    )
    db.add(leave)
    _audit(db, request, "employees", "leave_create", record=leave, record_type="leave", new_values={"employee_id": employee_id, "leave_type": leave_type})
    db.commit()
    return {"status": "recorded", "id": leave.id}


# Advances
@router.get("/advances")
def list_advances(employee_id: int | None = None, db: Session = Depends(get_db)):
    q = db.query(EmployeeAdvance)
    if employee_id:
        q = q.filter(EmployeeAdvance.employee_id == employee_id)
    return q.order_by(EmployeeAdvance.date.desc()).all()


@router.post("/advances")
def record_advance(payload: dict, request: Request, db: Session = Depends(get_db)):
    employee_id = (payload or {}).get("employee_id")
    date_str = (payload or {}).get("date")
    amount = (payload or {}).get("amount")
    if not employee_id or not date_str or amount is None:
        raise HTTPException(status_code=400, detail="employee_id, date, amount required")
    from datetime import datetime
    adv = EmployeeAdvance(
        employee_id=employee_id,
        date=datetime.fromisoformat(date_str).date(),
        amount=amount,
    )
    db.add(adv)
    _audit(db, request, "employees", "advance_create", record=adv, record_type="employee_advance", new_values={"employee_id": employee_id, "amount": float(amount)})
    db.commit()
    return {"status": "recorded", "id": adv.id}


# Deductions
@router.get("/deductions")
def list_deductions(employee_id: int | None = None, db: Session = Depends(get_db)):
    q = db.query(Deduction)
    if employee_id:
        q = q.filter(Deduction.employee_id == employee_id)
    return q.order_by(Deduction.date.desc()).all()


@router.post("/deductions")
def record_deduction(payload: dict, request: Request, db: Session = Depends(get_db)):
    employee_id = (payload or {}).get("employee_id")
    date_str = (payload or {}).get("date")
    amount = (payload or {}).get("amount")
    reason = (payload or {}).get("reason")
    if not employee_id or not date_str or amount is None:
        raise HTTPException(status_code=400, detail="employee_id, date, amount required")
    from datetime import datetime
    ded = Deduction(
        employee_id=employee_id,
        date=datetime.fromisoformat(date_str).date(),
        amount=amount,
        reason=reason,
    )
    db.add(ded)
    _audit(db, request, "employees", "deduction_create", record=ded, record_type="deduction", new_values={"employee_id": employee_id, "amount": float(amount)})
    db.commit()
    return {"status": "recorded", "id": ded.id}


# Payroll
@router.get("/payroll")
def list_payroll(
    employee_id: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    db: Session = Depends(get_db),
):
    from datetime import datetime
    q = db.query(SalaryPayment)
    if employee_id:
        q = q.filter(SalaryPayment.employee_id == employee_id)
    if from_date:
        q = q.filter(SalaryPayment.period_start >= datetime.fromisoformat(from_date).date())
    if to_date:
        q = q.filter(SalaryPayment.period_end <= datetime.fromisoformat(to_date).date())
    return q.order_by(SalaryPayment.period_end.desc()).all()


@router.post("/payroll/run")
def run_payroll(payload: dict, request: Request, db: Session = Depends(get_db)):
    """Run payroll for a period (admin/manager only)."""
    period_start = (payload or {}).get("period_start")
    period_end = (payload or {}).get("period_end")
    employee_ids = (payload or {}).get("employee_ids")  # optional: specific employees
    if not period_start or not period_end:
        raise HTTPException(status_code=400, detail="period_start and period_end required")

    from datetime import datetime
    period_start_dt = datetime.fromisoformat(period_start).date()
    period_end_dt = datetime.fromisoformat(period_end).date()

    employees = db.query(Employee).filter(Employee.status == EmployeeStatus.ACTIVE).all()
    if employee_ids:
        employees = [e for e in employees if e.id in employee_ids]

    created = []
    for emp in employees:
        # Calculate gross from single salary field
        gross = emp.salary or 0

        # Get advances in period
        advances = db.query(EmployeeAdvance).filter(
            EmployeeAdvance.employee_id == emp.id,
            EmployeeAdvance.given_date >= period_start_dt,
            EmployeeAdvance.given_date <= period_end_dt,
        ).all()
        total_advances = sum(float(a.amount or 0) for a in advances)

        # Get deductions in period
        deductions = db.query(Deduction).filter(
            Deduction.employee_id == emp.id,
            Deduction.date >= period_start_dt,
            Deduction.date <= period_end_dt,
        ).all()
        total_deductions = sum(float(d.amount or 0) for d in deductions)

        # Attendance days
        working_days = db.query(Attendance).filter(
            Attendance.employee_id == emp.id,
            Attendance.date >= period_start_dt,
            Attendance.date <= period_end_dt,
            Attendance.status.in_(["present", "half_day"]),
        ).count()

        # Simple calculation: gross - advances - deductions
        net = gross - total_advances - total_deductions

        period_month = period_start_dt.strftime("%Y-%m")
        payment = SalaryPayment(
            employee_id=emp.id,
            payment_date=_ist_wall(),
            period_month=period_month,
            basic_salary=gross,
            allowances=0,
            deductions=total_deductions,
            advance_recovered=total_advances,
            net_pay=net,
            payment_mode="bank",
        )
        db.add(payment)
        created.append(payment)

    _audit(db, request, "payroll", "run", record_type="payroll_run", new_values={"period_start": period_start, "period_end": period_end, "count": len(created)})
    db.commit()
    return {"status": "completed", "count": len(created), "payments": [{"id": p.id, "employee_id": p.employee_id, "net": float(p.net_pay or 0)} for p in created]}


@router.delete("/payroll/payments/{payment_id}")
def archive_payroll_payment(payment_id: int, request: Request, db: Session = Depends(get_db)):
    pay = db.query(SalaryPayment).filter(SalaryPayment.id == payment_id).first()
    if not pay:
        raise HTTPException(status_code=404, detail="Payroll payment not found")
    reason = _change_reason(request)
    if not _archive_record(db, request, pay, "payroll", "salary_payment"):
        raise HTTPException(status_code=409, detail="Payment already archived")
    _audit(db, request, "payroll", "archive", record=pay, record_type="salary_payment", old_values={"is_archived": False}, new_values={"is_archived": True}, notes=reason)
    db.commit()
    return {"status": "archived", "id": payment_id}