"""CS Assignment ORM model

Tracks assignment history for conversations (new in Alembic 0003).
"""
from sqlalchemy import (
    Column, BigInteger, String, DateTime, ForeignKey, Index,
)
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from backend.customer_service.models.conversation import CSBase

_now = lambda: datetime.now(timezone.utc)


class CSAssignment(CSBase):
    __tablename__ = "assignments"
    __table_args__ = (
        Index("idx_cs_assign_conv", "conversation_id", "assigned_at"),
        Index("idx_cs_assign_agent", "agent_id", "assigned_at"),
        {"schema": "customer_service"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id = Column(
        String(64),
        ForeignKey("customer_service.conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id = Column(
        String(64),
        ForeignKey("customer_service.cs_agents.agent_id", ondelete="SET NULL"),
        nullable=True,
    )

    assigned_by = Column(String(64), nullable=True,
                         comment="user_id of who/what made the assignment")
    assigned_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    unassigned_at = Column(DateTime(timezone=True), nullable=True)

    conversation = relationship("CSConversation", back_populates="assignments")
