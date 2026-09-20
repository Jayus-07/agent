"""CS Agent (human support agent) ORM model

Maps to ``customer_service.cs_agents`` table (new in Alembic 0003).
"""
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from backend.customer_service.models.conversation import CSBase


def _now():
    return datetime.now(timezone.utc)


class CSAgent(CSBase):
    __tablename__ = "cs_agents"
    __table_args__ = (
        Index("idx_cs_agent_email", "email", unique=True),
        Index(
            "uq_cs_agent_tenant_agent_id",
            "tenant_id",
            "agent_id",
            unique=True,
        ),
        Index(
            "idx_cs_agent_tenant_status",
            "tenant_id",
            "enabled",
            "accepting",
        ),
        Index(
            "uq_cs_agent_tenant_auth_user",
            "tenant_id",
            "auth_user_id",
            unique=True,
            postgresql_where=text("auth_user_id IS NOT NULL"),
        ),
        {"schema": "customer_service"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    agent_id = Column(String(64), unique=True, nullable=False)
    tenant_id = Column(String(64), nullable=False, default="default")

    display_name = Column(String(128), nullable=False)
    email = Column(String(128), nullable=True)
    role = Column(
        String(20), nullable=False, default="agent",
        comment="agent|supervisor|admin",
    )

    available = Column(Boolean, nullable=False, default=True)
    auth_user_id = Column(String(64), nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    accepting = Column(Boolean, nullable=False, default=True)
    last_assigned_at = Column(DateTime(timezone=True), nullable=True)
    version = Column(Integer, nullable=False, default=0)
    max_conversations = Column(BigInteger, nullable=False, default=10)

    custom_fields = Column(JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
