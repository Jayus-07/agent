"""SQLAlchemy ORM for memory_records"""
from uuid import uuid4
from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, Float, Integer, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base
from pgvector.sqlalchemy import Vector

Base = declarative_base()

class MemoryRecord(Base):
    __tablename__ = "memory_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(String(64), nullable=False, default="default")
    session_id = Column(String(128), nullable=False, default="")
    memory_type = Column(String(32), nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(512))
    importance_score = Column(Float, nullable=False, default=0.5)
    confidence_score = Column(Float, nullable=False, default=1.0)
    access_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_access_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expire_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    superseded_by = Column(UUID(as_uuid=True), ForeignKey("memory_records.id"), nullable=True)
