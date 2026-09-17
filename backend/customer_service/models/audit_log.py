"""CSAuditLog ORM model

Maps to ``customer_service.audit_logs`` (created by 006 raw SQL migration).
P1 重构（2026-09-17）启用：此前表已建但全代码库零写入，
高风险操作审计只活在 LangGraph state 里（audit-report §P0-7）。
"""
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from backend.customer_service.models.conversation import CSBase


def _now():
    return datetime.now(timezone.utc)


class CSAuditLog(CSBase):
    __tablename__ = "audit_logs"
    __table_args__ = ({"schema": "customer_service"},)

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    log_id = Column(String(64), unique=True, nullable=False)
    user_id = Column(String(64), nullable=False, index=True)
    conversation_id = Column(String(64), nullable=True)
    action_id = Column(String(64), nullable=True)
    actor_type = Column(String(20), nullable=False, default="ai")
    actor_id = Column(String(64), nullable=True)
    action = Column(String(50), nullable=False, index=True)
    resource_type = Column(String(30), nullable=True)
    resource_id = Column(String(64), nullable=True)
    before_state = Column(JSONB, nullable=True)
    after_state = Column(JSONB, nullable=True)
    result = Column(String(20), nullable=False)
    error_detail = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
