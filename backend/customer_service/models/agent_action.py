"""CSAgentAction ORM model

Maps to ``customer_service.agent_actions`` (created by 006 raw SQL migration).
P1 重构（2026-09-17）启用：此前表已建但全代码库零写入。
"""
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB

from backend.customer_service.models.conversation import CSBase


def _now():
    return datetime.now(timezone.utc)


class CSAgentAction(CSBase):
    __tablename__ = "agent_actions"
    __table_args__ = ({"schema": "customer_service"},)

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    action_id = Column(String(64), unique=True, nullable=False)
    conversation_id = Column(String(64), nullable=False, index=True)
    user_id = Column(String(64), nullable=False, index=True)
    action_type = Column(String(30), nullable=False)
    target_type = Column(String(30), nullable=True)
    target_id = Column(String(64), nullable=True)
    before_state = Column(JSONB, nullable=True)
    after_state = Column(JSONB, nullable=True)
    confirmation_state = Column(String(20), nullable=False, default="pending")
    status = Column(String(20), nullable=False, default="pending")
    executed_by = Column(String(20), nullable=False, default="ai")
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    executed_at = Column(DateTime(timezone=True), nullable=True)
