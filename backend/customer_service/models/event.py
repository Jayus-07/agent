"""CS Event ORM model — realtime 事件持久化（P3.2）

Maps to ``customer_service.events`` table. 所有坐席/用户侧实时事件
（message.created / conversation.waiting / ...）统一落库：

- id          全局自增，同时充当「会话内游标 seq」（单调即可，无需会话内连续）
- event_id    客户端幂等去重键（UNIQUE，重复投递可安全忽略）
- payload     原有平铺字段原样入 JSONB

断线补发：GET /cs/conversations/{id}/events?after_seq=N 按 id > N 回放。
"""
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from backend.customer_service.models.conversation import CSBase


def _now():
    return datetime.now(timezone.utc)


class CSEvent(CSBase):
    __tablename__ = "events"
    __table_args__ = (
        # 断线补发主查询：WHERE conversation_id AND id > after_seq ORDER BY id
        Index("idx_cs_event_conv_id", "conversation_id", "id"),
        Index(
            "uq_cs_event_tenant_handoff_seq",
            "tenant_id",
            "handoff_id",
            "event_seq",
            unique=True,
        ),
        Index(
            "idx_cs_event_outbox_pending",
            "outbox_status",
            "created_at",
            "id",
            postgresql_where=text("outbox_status = 'pending'"),
        ),
        {"schema": "customer_service"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id = Column(String(64), nullable=False)
    event_id = Column(String(64), unique=True, nullable=False)
    tenant_id = Column(String(64), nullable=False, default="default")
    handoff_id = Column(String(64), nullable=True)
    target_agent_id = Column(String(64), nullable=True)
    actor_user_id = Column(String(64), nullable=True)
    event_seq = Column(BigInteger, nullable=True)
    type = Column(String(64), nullable=False)
    payload = Column(JSONB, nullable=False, default=dict)
    outbox_status = Column(String(20), nullable=False, default="pending")
    published_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
