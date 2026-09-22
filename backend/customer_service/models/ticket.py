"""CSTicket ORM model — customer_service.tickets（migration 036，批次C）。

统一工单：投诉工单（此前仅内存模拟）与转人工单（handoffs.ticket_id 游离
字符串）收编落库。字段语义见 migration 文件头注释。
"""
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    String,
    Text,
)

from backend.customer_service.models.conversation import CSBase


def _now():
    return datetime.now(timezone.utc)


TICKET_TYPES = ("complaint", "handoff", "inquiry", "repair")
TICKET_STATUSES = ("open", "processing", "pending_user", "resolved", "closed")
TICKET_SOURCES = ("ai", "agent", "user")
TICKET_PRIORITIES = ("low", "medium", "high", "critical")

# 状态机（线性，无回环；复用 BusinessRuleError 报非法流转）
VALID_TICKET_TRANSITIONS: dict[str, frozenset[str]] = {
    "open": frozenset({"processing", "pending_user", "resolved", "closed"}),
    "processing": frozenset({"pending_user", "resolved", "closed"}),
    "pending_user": frozenset({"processing", "resolved", "closed"}),
    "resolved": frozenset({"closed"}),
    "closed": frozenset(),
}


class CSTicket(CSBase):
    __tablename__ = "tickets"
    __table_args__ = (
        Index("uq_cs_tickets_tenant_ticket_id", "tenant_id", "ticket_id",
              unique=True),
        Index("idx_cs_tickets_user_status", "tenant_id", "user_id", "status",
              "created_at"),
        Index("idx_cs_tickets_tenant_status", "tenant_id", "status",
              "priority", "created_at"),
        Index("idx_cs_tickets_conversation", "conversation_id"),
        {"schema": "customer_service"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ticket_id = Column(String(64), nullable=False)
    tenant_id = Column(String(64), nullable=False, default="default")
    type = Column(String(30), nullable=False, default="inquiry")
    status = Column(String(30), nullable=False, default="open")
    source = Column(String(30), nullable=False, default="ai")
    conversation_id = Column(String(64), nullable=False)
    user_id = Column(String(64), nullable=False)
    handoff_id = Column(String(64), nullable=True)
    assigned_agent_id = Column(String(64), nullable=True)
    priority = Column(String(10), nullable=False, default="medium")
    title = Column(String(200), nullable=False, default="")
    description = Column(Text, nullable=True)
    resolution = Column(Text, nullable=True)
    severity = Column(String(20), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now,
                        nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
