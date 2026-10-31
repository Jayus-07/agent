"""CS Canned Response（坐席快捷回复话术）ORM model

Maps to ``customer_service.cs_canned_responses`` table (created by SQL
migration backend/sql/migrations/127_cs_canned_responses.sql，经
scripts/init_db.py 执行).

权限语义（cs-workstation-v2 P4）：personal 仅 owner 读写；team 全员可读、
supervisor 可写——查询/变更的谓词收口在路由层（cs_canned.py）。
"""
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Index,
    String,
    Text,
)

from backend.customer_service.models.conversation import CSBase


def _now():
    return datetime.now(timezone.utc)


class CSCannedResponse(CSBase):
    __tablename__ = "cs_canned_responses"
    __table_args__ = (
        Index(
            "idx_canned_tenant_scope_owner",
            "tenant_id",
            "scope",
            "owner_agent_id",
        ),
        {"schema": "customer_service"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id = Column(String(64), nullable=False)
    scope = Column(
        String(16), nullable=False,
        comment="personal|team",
    )
    owner_agent_id = Column(
        String(64), nullable=True,
        comment="personal 必填；team 为 NULL",
    )
    title = Column(String(80), nullable=False)
    content = Column(Text, nullable=False)
    category = Column(String(32), nullable=False, default="通用", server_default="通用")
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
