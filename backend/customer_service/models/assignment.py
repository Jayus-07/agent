"""CS Assignment ORM model

Tracks assignment history for conversations (new in Alembic 0003).
"""
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import relationship

from backend.customer_service.models.conversation import CSBase


def _now():
    return datetime.now(timezone.utc)


ASSIGNMENT_STATES = (
    "offered",
    "accepted",
    "declined",
    "expired",
    "released",
    "closed",
)


class CSAssignment(CSBase):
    __tablename__ = "assignments"
    __table_args__ = (
        CheckConstraint(
            "state NOT IN ('offered', 'accepted') OR handoff_id IS NOT NULL",
            name="ck_cs_assignment_active_handoff_required",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "handoff_id"],
            [
                "customer_service.handoffs.tenant_id",
                "customer_service.handoffs.handoff_id",
            ],
            name="fk_cs_assignment_tenant_handoff",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "agent_id"],
            [
                "customer_service.cs_agents.tenant_id",
                "customer_service.cs_agents.agent_id",
            ],
            name="fk_cs_assignment_tenant_agent",
        ),
        Index("idx_cs_assign_conv", "conversation_id", "assigned_at"),
        Index("idx_cs_assign_agent", "agent_id", "assigned_at"),
        Index(
            "uq_cs_assignment_tenant_handoff_active",
            "tenant_id",
            "handoff_id",
            unique=True,
            postgresql_where=text(
                "handoff_id IS NOT NULL AND state IN ('offered', 'accepted')"
            ),
        ),
        Index(
            "idx_cs_assignment_tenant_agent_state",
            "tenant_id",
            "agent_id",
            "state",
        ),
        {"schema": "customer_service"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id = Column(String(64), nullable=False, default="default")
    handoff_id = Column(String(64), nullable=True)
    conversation_id = Column(
        String(64),
        ForeignKey("customer_service.conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id = Column(String(64), nullable=True)

    state = Column(
        String(20),
        nullable=False,
        default="offered",
        comment="offered|accepted|declined|expired|released|closed",
    )
    attempt_no = Column(Integer, nullable=False, default=1)
    offer_version = Column(Integer, nullable=False, default=0)
    offered_at = Column(DateTime(timezone=True), nullable=True)
    offer_expires_at = Column(DateTime(timezone=True), nullable=True)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    declined_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    assigned_by = Column(String(64), nullable=True,
                         comment="user_id of who/what made the assignment")
    assigned_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    unassigned_at = Column(DateTime(timezone=True), nullable=True)

    conversation = relationship("CSConversation", back_populates="assignments")
