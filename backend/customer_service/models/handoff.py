"""CSHandoff ORM model

Maps to ``customer_service.handoffs`` (created by Alembic 0004 migration).
"""
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)

from backend.customer_service.models.conversation import CSBase


def _now():
    return datetime.now(timezone.utc)


HANDOFF_STATES = (
    "ai_active",
    "initiated",
    "handoff_requested",
    "waiting_human",
    "agent_offered",
    "human_active",
    "closed",
)


class CSHandoff(CSBase):
    __tablename__ = "handoffs"
    __table_args__ = (
        Index(
            "idx_cs_handoff_active",
            "user_id",
            "handoff_state",
            postgresql_where=text("handoff_state != 'closed'"),
        ),
        Index(
            "uq_cs_handoff_tenant_conversation_active",
            "tenant_id",
            "conversation_id",
            unique=True,
            postgresql_where=text("handoff_state <> 'closed'"),
        ),
        Index(
            "idx_cs_handoff_dispatch_queue",
            "tenant_id",
            "handoff_state",
            text("priority DESC"),
            "created_at",
            "id",
        ),
        {"schema": "customer_service"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    handoff_id = Column(String(64), unique=True, nullable=False)
    conversation_id = Column(String(64), nullable=False)
    user_id = Column(String(64), nullable=False, index=True)
    tenant_id = Column(String(64), nullable=False, default="default")
    handoff_state = Column(
        String(20),
        nullable=False,
        default="initiated",
        comment="ai_active|initiated|handoff_requested|waiting_human|agent_offered|human_active|closed",
    )
    trigger_type = Column(String(30), nullable=True)
    trigger_reason = Column(Text, nullable=True)
    ticket_id = Column(String(64), nullable=True)
    priority = Column(Integer, nullable=False, default=50)
    assigned_agent_id = Column(
        String(64),
        ForeignKey("customer_service.cs_agents.agent_id", ondelete="SET NULL"),
        nullable=True,
    )
    assignment_version = Column(Integer, nullable=False, default=0)
    attempt_count = Column(Integer, nullable=False, default=0)
    idempotency_key = Column(String(128), nullable=True)
    total_deadline_at = Column(DateTime(timezone=True), nullable=True)
    offered_at = Column(DateTime(timezone=True), nullable=True)
    offer_expires_at = Column(DateTime(timezone=True), nullable=True)
    closed_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
    closed_at = Column(DateTime(timezone=True), nullable=True)
