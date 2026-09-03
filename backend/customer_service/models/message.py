"""CS Message ORM model

Maps to ``customer_service.messages`` table.
Extends the 006 schema with sender_type / content_type / private / attachments.
"""
from sqlalchemy import (
    Column, BigInteger, String, Text, Boolean, DateTime, Float,
    ForeignKey, Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from backend.customer_service.models.conversation import CSBase

_now = lambda: datetime.now(timezone.utc)


class CSMessage(CSBase):
    __tablename__ = "messages"
    __table_args__ = (
        Index("idx_cs_msg_conv", "conversation_id", "created_at"),
        Index("idx_cs_msg_intent", "intent_domain", "intent_name"),
        {"schema": "customer_service"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    message_id = Column(String(64), unique=True, nullable=False)
    conversation_id = Column(
        String(64),
        ForeignKey("customer_service.conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
    )

    # ── sender ──
    sender_type = Column(
        String(20), nullable=False, default="user",
        comment="user|assistant|system|human_agent",
    )
    sender_id = Column(String(64), nullable=True)

    # ── content ──
    content = Column(Text, nullable=False)
    content_type = Column(
        String(20), nullable=False, default="text",
        comment="text|image|file|card|template",
    )
    attachments = Column(JSONB, nullable=True)

    # ── classification ──
    intent_domain = Column(String(30), nullable=True)
    intent_name = Column(String(50), nullable=True)
    confidence = Column(Float, nullable=True)

    # ── visibility ──
    private = Column(Boolean, nullable=False, default=False,
                     comment="Internal note — invisible to end user")

    # ── metadata ──
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)

    # ── timestamps ──
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)

    # ── relationships ──
    conversation = relationship("CSConversation", back_populates="messages")
