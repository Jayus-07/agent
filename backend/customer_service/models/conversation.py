"""CS Conversation ORM model

Dual-dimension state:
  conversation_status  — open / pending / resolved / snoozed
  handling_mode        — ai / human / waiting_human

Maps to ``customer_service.conversations`` table (created by 006 migration,
extended by Alembic 0003).
"""
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import declarative_base, relationship

CSBase = declarative_base()

def _now():
    return datetime.now(timezone.utc)


class CSConversation(CSBase):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("idx_cs_conv_user", "user_id", "updated_at"),
        Index("idx_cs_conv_status", "conversation_status",
              postgresql_where=Text("conversation_status != 'resolved'")),
        Index("idx_cs_conv_agent", "assigned_agent_id",
              postgresql_where=Text("assigned_agent_id IS NOT NULL")),
        {"schema": "customer_service"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id = Column(String(64), unique=True, nullable=False)
    user_id = Column(String(64), nullable=False)

    # ── dual-dimension state ──
    conversation_status = Column(
        String(20), nullable=False, default="open",
        comment="open|pending|resolved|snoozed",
    )
    handling_mode = Column(
        String(20), nullable=False, default="ai",
        comment="ai|human|waiting_human",
    )

    # ── routing / assignment ──
    channel = Column(String(20), nullable=False, default="web")
    inbox_id = Column(String(64), nullable=True)
    assigned_agent_id = Column(
        String(64),
        ForeignKey("customer_service.cs_agents.agent_id", ondelete="SET NULL"),
        nullable=True,
    )
    team_id = Column(String(64), nullable=True)

    # ── classification ──
    priority = Column(String(10), nullable=False, default="medium")
    labels = Column(ARRAY(String), nullable=False, default=list)

    # ── AI toggle (legacy compat, kept for 006 backward compat) ──
    ai_enabled = Column(Boolean, nullable=False, default=True)

    # ── content ──
    summary = Column(Text, nullable=True)
    context_summary = Column(Text, nullable=True)

    # ── timestamps ──
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    first_reply_at = Column(DateTime(timezone=True), nullable=True)
    last_activity_at = Column(DateTime(timezone=True), nullable=True)

    # ── relationships ──
    messages = relationship(
        "CSMessage", back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="CSMessage.created_at",
    )
    assignments = relationship(
        "CSAssignment", back_populates="conversation",
        cascade="all, delete-orphan",
    )

    @property
    def is_open(self) -> bool:
        return self.conversation_status == "open"

    @property
    def is_resolved(self) -> bool:
        return self.conversation_status == "resolved"

    @property
    def is_ai_handling(self) -> bool:
        return self.handling_mode == "ai"
