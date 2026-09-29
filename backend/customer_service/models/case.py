"""customer_service/models/case.py — 统一案件 cs_case ORM（迁移 B12）

设计方案 §10.1 核心字段落位；schema 跟随现役 CS 表（customer_service）。
状态机：open → waiting_user / processing → resolved / closed；
SLA 按 P0/P1/P2 = 5min / 30min / 24h（设计方案 §4.6）。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.customer_service.models.conversation import CSBase

CASE_PRIORITIES = ("P0", "P1", "P2")
CASE_STATUSES = ("open", "waiting_user", "processing", "resolved", "closed")
CASE_TYPES = ("refund", "return", "exchange", "complaint", "inquiry")
# 活跃生命周期（部分唯一索引 uq_cs_case_active 的谓词，两处必须成对改）
CASE_ACTIVE_STATUSES = ("open", "waiting_user", "processing")
CASE_SLA_MINUTES = {"P0": 5, "P1": 30, "P2": 24 * 60}


def compute_sla_deadline(priority: str) -> datetime:
    """SLA 时限：按优先级从当前时刻起算（设计方案 §4.6 SLA 表）。"""
    minutes = CASE_SLA_MINUTES.get(priority, CASE_SLA_MINUTES["P2"])
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


def new_case_id() -> str:
    return str(uuid.uuid4())


class CSCase(CSBase):
    """统一案件：工单/投诉/售后收敛实体（聊天 ≠ 问题，案件可跨会话）。"""

    __tablename__ = "cs_case"
    __table_args__ = ({"schema": "customer_service"},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), default=new_case_id, nullable=False, unique=True)
    tenant_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False)
    conversation_id: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    case_type: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[str] = mapped_column(String(8), default="P2", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="open", nullable=False)
    title: Mapped[str] = mapped_column(Text, default="", nullable=False)
    context: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    owner_agent_id: Mapped[str | None] = mapped_column(String(64))
    related_confirmation_id: Mapped[str | None] = mapped_column(Text)
    resolution: Mapped[str | None] = mapped_column(Text)
    sla_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc))
