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


class EmployeeStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ON_LEAVE = "on_leave"


class EmployeeType(str, enum.Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACTOR = "contractor"


class Employee(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(String(100), nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=True)
    phone = Column(String(15), nullable=True)
    address = Column(Text, nullable=True)
    employee_type = Column(SQLEnum(EmployeeType), nullable=False)
    status = Column(SQLEnum(EmployeeStatus), default=EmployeeStatus.ACTIVE)
    joining_date = Column(DateTime(timezone=True), nullable=True)
    salary = Column(Numeric(15, 2), default=0)
    bank_account_no = Column(String(50), nullable=True)
    ifsc_code = Column(String(11), nullable=True)
    pan_no = Column(String(10), nullable=True)
    aadhaar_no = Column(String(12), nullable=True)
    emergency_contact = Column(String(15), nullable=True)
    emergency_name = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)
    updated_at = Column(DateTime(timezone=True), onupdate=_ist_wall)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    attendance = relationship("Attendance", back_populates="employee", cascade="all, delete-orphan")
    leaves = relationship("Leave", back_populates="employee", cascade="all, delete-orphan")
    salary_payments = relationship(
        "SalaryPayment", back_populates="employee", cascade="all, delete-orphan"
    )
    advances = relationship(
        "EmployeeAdvance", back_populates="employee", cascade="all, delete-orphan"
    )
    deductions = relationship("Deduction", back_populates="employee", cascade="all, delete-orphan")


class Attendance(Base):
    """One attendance mark per employee per day.

    UNIQUE(employee_id, date) because the natural failure mode of a paper
    register is the same day being entered twice, which silently double-counts
    in every attendance report. Re-marking a day is an update, not a new row.
    """

    __tablename__ = "attendance"
    __table_args__ = (UniqueConstraint("employee_id", "date", name="uq_attendance_employee_date"),)

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    date = Column(DateTime(timezone=True), nullable=False)
    check_in = Column(String(10), nullable=True)
    check_out = Column(String(10), nullable=True)
    hours_worked = Column(Numeric(5, 2), default=0)
    status = Column(String(20), default="present")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)

    employee = relationship("Employee", back_populates="attendance")


class Leave(Base):
    __tablename__ = "leaves"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    start_date = Column(DateTime(timezone=True), nullable=False)
    end_date = Column(DateTime(timezone=True), nullable=False)
    leave_type = Column(String(30), nullable=False)  # casual, sick, earned, etc.
    days = Column(Numeric(5, 2), nullable=False)
    reason = Column(Text, nullable=True)
    status = Column(String(20), default="pending")
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)

    employee = relationship("Employee", back_populates="leaves")


class SalaryPayment(Base):
    """One month's pay for one employee.

    `period_month` ('2026-09') is what makes this a payroll record rather than
    an anonymous payment, and UNIQUE(employee_id, period_month) is what makes it
    impossible to pay the same month twice. The easiest and most expensive
    mistake in a payroll system is a duplicate run, and a check in the request
    handler can be raced; a constraint cannot.

    net_pay is COMPUTED by services/payroll.py and stored so the historical
    register cannot change when a rate or an advance balance later moves.
    A supplied net that disagrees with the formula is refused.
    """

    __tablename__ = "salary_payments"
    __table_args__ = (
        UniqueConstraint("employee_id", "period_month", name="uq_salary_payment_period"),
    )

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    payment_date = Column(DateTime(timezone=True), nullable=False)
    # YYYY-MM. Added because the original table had no period at all, which
    # made "which month was this for?" unanswerable and double-payment easy.
    period_month = Column(String(7), nullable=True, index=True)
    basic_salary = Column(Numeric(15, 2), default=0)
    allowances = Column(Numeric(15, 2), default=0)
    deductions = Column(Numeric(15, 2), default=0)
    # Advance recovered this period, kept apart from `deductions` so a reader
    # can tell a salary advance from a fine.
    advance_recovered = Column(Numeric(15, 2), default=0)
    net_pay = Column(Numeric(15, 2), default=0)
    # Cash / bank / UPI, matching the other financial tables.
    payment_mode = Column(String(30), default="bank")
    notes = Column(Text, nullable=True)
    is_archived = Column(Boolean, default=False, nullable=False)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    archived_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    employee = relationship("Employee", back_populates="salary_payments")


class EmployeeAdvance(Base):
    __tablename__ = "employee_advances"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    advance_amount = Column(Numeric(15, 2), default=0)
    given_date = Column(DateTime(timezone=True), nullable=False)
    return_date = Column(DateTime(timezone=True), nullable=True)
    outstanding = Column(Numeric(15, 2), default=0)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)

    employee = relationship("Employee", back_populates="advances")


class Deduction(Base):
    """A deduction against one employee's pay for one period.

    `period_month` links a deduction to the payroll run that applied it, so a
    figure cannot be deducted twice across runs and the run can show what it
    took and why.
    """

    __tablename__ = "deductions"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    deduction_type = Column(String(30), nullable=False)  # tax, fine, other
    amount = Column(Numeric(15, 2), default=0)
    date = Column(DateTime(timezone=True), nullable=False)
    period_month = Column(String(7), nullable=True, index=True)
    reason = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    # A deduction consumed by a payroll run is kept but marked, rather than
    # deleted, so the register still explains itself.
    is_applied = Column(Boolean, default=False, nullable=False)
    applied_in = Column(String(7), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    employee = relationship("Employee", back_populates="deductions")
