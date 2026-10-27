"""Stored document counters, one row per series.

Backs the sequential numbering in services/document_sequence.py. A counter is
stored rather than derived from `MAX(number) + 1` because deriving it would
reintroduce a gap the moment a document were deleted or archived - which is
precisely when a gap in a tax series looks most like a suppressed document.

`key` is "<PREFIX>|<financial year>", e.g. "CH|26-27", so each financial year
starts its own series while the counter itself persists across restarts.
"""

from sqlalchemy import Column, DateTime, Integer, String

from backend.app.core.database import Base, _ist_wall


class DocumentSequence(Base):
    __tablename__ = "document_sequences"

    # Deliberately not a primary key: the upsert relies on a UNIQUE constraint
    # for ON CONFLICT to target. A primary key would also work, but "key" reads
    # as a natural identifier here and the intent is clearer.
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(60), unique=True, index=True, nullable=False)
    last_value = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), default=_ist_wall, onupdate=_ist_wall)
