import enum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import relationship

from backend.app.core.database import Base, _ist_wall


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    DATA_ENTRY = "data_entry"
    VIEWER = "viewer"
    CHALLAN_ENTRY = "challan_entry"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(100), nullable=True)
    role = Column(SQLEnum(UserRole), default=UserRole.VIEWER, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)
    updated_at = Column(DateTime(timezone=True), onupdate=_ist_wall)

    audit_logs = relationship("AuditLog", back_populates="user")
    dashboard_preferences = relationship(
        "DashboardPreference", back_populates="user", uselist=False
    )


class DashboardPreference(Base):
    __tablename__ = "dashboard_preferences"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    show_sales = Column(Boolean, default=True)
    show_production = Column(Boolean, default=True)
    show_inventory = Column(Boolean, default=True)
    show_expenses = Column(Boolean, default=True)
    show_outstanding = Column(Boolean, default=True)
    show_purchases = Column(Boolean, default=True)
    show_employees = Column(Boolean, default=True)
    show_recent_transactions = Column(Boolean, default=True)
    # "light" or "dark". Nullable so pre-theme rows read NULL, which the
    # endpoints treat as light — same safety as the other nullable adds.
    theme = Column(String(10), default="light")
    created_at = Column(DateTime(timezone=True), default=_ist_wall)
    updated_at = Column(DateTime(timezone=True), onupdate=_ist_wall)

    user = relationship("User", back_populates="dashboard_preferences")


class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, index=True, nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)


class Permission(Base):
    __tablename__ = "permissions"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, index=True, nullable=False)
    module = Column(String(50), nullable=False)
    action = Column(String(50), nullable=False)
    description = Column(Text, nullable=True)


class RolePermission(Base):
    """Admin-configurable override for one (role, permission) pair.

    A row exists only when an Admin has made an explicit choice. `granted`
    True grants it, False denies it. No row at all means "use the built-in
    default for that role", which keeps behaviour unchanged until someone
    actually configures it.
    """

    __tablename__ = "role_permissions"

    id = Column(Integer, primary_key=True, index=True)
    role = Column(String(30), nullable=False, index=True)  # admin|manager|data_entry|viewer
    permission = Column(String(100), nullable=False, index=True)  # permissions.name
    granted = Column(Boolean, default=True, nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=_ist_wall)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    __table_args__ = (UniqueConstraint("role", "permission", name="uq_role_permission"),)
