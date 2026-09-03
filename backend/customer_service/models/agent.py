"""CS Agent (human support agent) ORM model

Maps to ``customer_service.cs_agents`` table (new in Alembic 0003).
"""
from sqlalchemy import (
    Column, BigInteger, String, Text, Boolean, DateTime, Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime, timezone

from backend.customer_service.models.conversation import CSBase

_now = lambda: datetime.now(timezone.utc)


class CSAgent(CSBase):
    __tablename__ = "cs_agents"
    __table_args__ = (
        Index("idx_cs_agent_email", "email", unique=True),
        {"schema": "customer_service"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    agent_id = Column(String(64), unique=True, nullable=False)

    display_name = Column(String(128), nullable=False)
    email = Column(String(128), nullable=True)
    role = Column(
        String(20), nullable=False, default="agent",
        comment="agent|supervisor|admin",
    )

    available = Column(Boolean, nullable=False, default=True)
    max_conversations = Column(BigInteger, nullable=False, default=10)

    custom_fields = Column(JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
