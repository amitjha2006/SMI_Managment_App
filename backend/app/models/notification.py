import enum

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy import Enum as SQLEnum

from backend.app.core.database import Base, _ist_wall


class NotificationType(str, enum.Enum):
    LOW_STOCK = "low_stock"
    DELIVERY_TARGET = "delivery_target"
    NEW_USER = "new_user"
    SYSTEM = "system"


class NotificationRule(Base):
    __tablename__ = "notification_rules"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    notification_type = Column(SQLEnum(NotificationType), nullable=False)
    target_type = Column(String(30), nullable=False)  # raw_material, product, customer, etc.
    target_id = Column(Integer, nullable=True)
    is_enabled = Column(Boolean, default=True)
    provider = Column(String(50), nullable=True)  # email, whatsapp, sms
    recipient = Column(String(200), nullable=True)
    template = Column(Text, nullable=True)
    threshold_value = Column(Numeric(15, 3), default=0)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)
    updated_at = Column(DateTime(timezone=True), onupdate=_ist_wall)


class NotificationLog(Base):
    __tablename__ = "notification_logs"

    id = Column(Integer, primary_key=True, index=True)
    rule_id = Column(Integer, ForeignKey("notification_rules.id"), nullable=True)
    recipient = Column(String(200), nullable=False)
    message = Column(Text, nullable=False)
    status = Column(String(30), nullable=False)  # sent, failed, pending
    error_message = Column(Text, nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)
