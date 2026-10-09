"""CSConfirmation ORM model

Maps to ``customer_service.confirmations`` (created by 006 raw SQL migration).
"""
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB

from backend.customer_service.models.conversation import CSBase


def _now():
    return datetime.now(timezone.utc)


class CSConfirmation(CSBase):
    __tablename__ = "confirmations"
    __table_args__ = ({"schema": "customer_service"},)

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    confirmation_id = Column(String(64), unique=True, nullable=False)
    conversation_id = Column(String(64), nullable=False)
    user_id = Column(String(64), nullable=False, index=True)
    action_type = Column(String(30), nullable=False)
    target_type = Column(String(30), nullable=False)
    target_id = Column(String(64), nullable=False)
    # Phase3 STOP D：业务操作身份（migration 051）。nullable = legacy 行
    # 不回填不参与唯一守卫；新生产写入由 business_guard fail-closed 强制。
    tenant_id = Column(String(64), nullable=True, index=True)
    semantic_fingerprint = Column(String(64), nullable=True)
    proposal = Column(JSONB, nullable=False)
    # 乐观版本用于确认卡绑定当前提案；历史行从迁移默认值 1 起算。
    proposal_version = Column(Integer, nullable=False, default=1, server_default="1")
    # 前端幂等键；仅一次性认领请求写入，同租户同用户不可复用。
    client_action_id = Column(String(36), nullable=True)
    state = Column(String(20), nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    executed_at = Column(DateTime(timezone=True), nullable=True)
