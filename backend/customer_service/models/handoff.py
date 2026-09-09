"""CSHandoff ORM model

Maps to ``customer_service.handoffs`` (created by Alembic 0004 migration).
"""
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, Index, String, Text

from backend.customer_service.models.conversation import CSBase


def _now():
    return datetime.now(timezone.utc)


class CSHandoff(CSBase):
    __tablename__ = "handoffs"
    __table_args__ = (
        Index(
            "idx_cs_handoff_active",
            "user_id",
            "handoff_state",
            postgresql_where=Text("handoff_state != 'closed'"),
        ),
        {"schema": "customer_service"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    handoff_id = Column(String(64), unique=True, nullable=False)
    conversation_id = Column(String(64), nullable=False)
    user_id = Column(String(64), nullable=False, index=True)
    handoff_state = Column(String(20), nullable=False, default="initiated")
    trigger_type = Column(String(30), nullable=True)
    trigger_reason = Column(Text, nullable=True)
    ticket_id = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
    closed_at = Column(DateTime(timezone=True), nullable=True)
