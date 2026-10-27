from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from backend.app.core.database import Base, _ist_wall


class PrintFooter(Base):
    """Named print footers (terms & conditions) for challans and invoices.

    The company profile holds a single legacy footer; this master list allows
    several, picked per challan and frozen into its company_terms at creation.
    Deleting a footer is safe: issued documents keep their frozen text.
    """

    __tablename__ = "print_footers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(80), unique=True, index=True, nullable=False)
    body = Column(Text, nullable=False, default="")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=_ist_wall)
    updated_at = Column(DateTime(timezone=True), onupdate=_ist_wall)
