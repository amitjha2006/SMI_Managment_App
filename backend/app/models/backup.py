from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, Numeric

from backend.app.core.database import Base, _ist_wall


class HSNCode(Base):
    __tablename__ = "hsn_codes"

    code = Column(String(12), primary_key=True)
    description = Column(String(200))
    gst_rate = Column(Numeric(5, 2), default=0)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)
    updated_at = Column(DateTime(timezone=True), onupdate=_ist_wall)


class ApplicationSetting(Base):
    __tablename__ = "application_settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    category = Column(String(50), nullable=True)  # notifications, backup, pricing, etc.
    is_editable = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)
    updated_at = Column(DateTime(timezone=True), onupdate=_ist_wall)


class BackupRecord(Base):
    __tablename__ = "backup_records"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(200), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer, default=0)
    backup_date = Column(DateTime(timezone=True), nullable=False)
    description = Column(Text, nullable=True)
    is_auto = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)
    created_by = Column(Integer, nullable=True)


class ImportReport(Base):
    """Persisted per-import result: counts plus row-level reasons.

    The on-screen result vanishes on navigation; this keeps recent
    reports (errors + skipped rows, capped like the response) so a bad
    import can be diagnosed later. Pruned to a small ring.
    """

    __tablename__ = "import_reports"

    id = Column(Integer, primary_key=True, index=True)
    import_type = Column(String(50), nullable=False)
    filename = Column(String(200), nullable=True)
    total_rows = Column(Integer, default=0)
    imported = Column(Integer, default=0)
    skipped = Column(Integer, default=0)
    failed = Column(Integer, default=0)
    errors = Column(Text, nullable=True)  # JSON list, capped like the response
    skipped_rows = Column(Text, nullable=True)  # JSON list, capped like the response
    created_at = Column(DateTime(timezone=True), default=_ist_wall)


class DashboardShare(Base):
    """A point-in-time public snapshot of dashboard figures."""

    __tablename__ = "dashboard_shares"

    id = Column(Integer, primary_key=True, index=True)
    link_id = Column(String(32), unique=True, index=True, nullable=False)
    payload = Column(Text, nullable=False)  # JSON snapshot
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)


class SavedReport(Base):
    """A saved custom-report definition (or link preset)."""

    __tablename__ = "saved_reports"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    kind = Column(String(10), nullable=False, default="custom")  # custom | link
    source = Column(String(30), nullable=True)  # custom-report source
    columns = Column(Text, nullable=True)  # JSON list
    from_date = Column(String(10), nullable=True)
    to_date = Column(String(10), nullable=True)
    target = Column(String(100), nullable=True)  # link target, e.g. "#/analysis"
    is_preset = Column(Boolean, default=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)
