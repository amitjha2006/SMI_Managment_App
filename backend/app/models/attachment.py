from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from backend.app.core.database import Base, _ist_wall


class Attachment(Base):
    __tablename__ = "attachments"

    id = Column(Integer, primary_key=True, index=True)
    file_name = Column(String(200), nullable=False)
    file_path = Column(String(500), nullable=False)  # relative or absolute path
    file_type = Column(String(100), nullable=True)  # mime type or extension
    file_size = Column(Integer, default=0)
    related_type = Column(
        String(50), nullable=False
    )  # customer, supplier, invoice, production, etc.
    related_id = Column(Integer, nullable=True)
    description = Column(Text, nullable=True)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    uploaded_at = Column(DateTime(timezone=True), default=_ist_wall)

    uploader = relationship("User", foreign_keys=[uploaded_by])


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action = Column(String(50), nullable=False)  # create, update, delete, confirm, etc.
    module = Column(String(50), nullable=False)  # customers, suppliers, production, etc.
    record_id = Column(Integer, nullable=True)  # the affected record's id
    record_type = Column(String(50), nullable=True)  # customer, invoice, etc.
    old_values = Column(Text, nullable=True)  # JSON string
    new_values = Column(Text, nullable=True)  # JSON string
    timestamp = Column(DateTime(timezone=True), default=_ist_wall, index=True)
    ip_address = Column(String(50), nullable=True)
    notes = Column(Text, nullable=True)

    user = relationship("User", back_populates="audit_logs")
