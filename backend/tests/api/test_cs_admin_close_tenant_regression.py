"""客服关闭会话的跨租户回归（2026-11-08 安全审计）。

指纹：``cs-claim-close.missing-tenant-predicate`` 的 close 分支。

claim 的租户谓词已有 SQL 级断言；close 是破坏性操作（把会话置 closed、
清 assigned_agent_id、写 outbox），必须用真实 PostgreSQL 验证：
他租坐席关闭 → 404 且状态不变；本租坐席关闭 → 成功。
"""
from __future__ import annotations

import uuid

import psycopg2
import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select
from starlette.requests import Request

from backend.app.api.routes import cs_admin
from backend.config.auth import (
    AUTH_TYPE_HEADER,
    TENANT_ID_HEADER,
    USER_ID_HEADER,
    USER_ROLES_HEADER,
)
from backend.config.database import MEMORY_DB_CONFIG
from backend.customer_service.models.agent import CSAgent
from backend.customer_service.models.assignment import CSAssignment
from backend.customer_service.models.conversation import CSConversation
from backend.customer_service.models.event import CSEvent
from backend.customer_service.models.handoff import CSHandoff

pytestmark = pytest.mark.asyncio


def _require_cs_pg() -> None:
    """客服表未迁移或 PG 不可达时跳过，不用 fake 冒充跨租户验收。"""
    try:
        with psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=2) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT to_regclass('customer_service.handoffs'), "
                    "to_regclass('customer_service.conversations'), "
                    "to_regclass('customer_service.cs_agents'), "
                    "to_regclass('customer_service.events')"
                )
                tables = cursor.fetchone()
    except Exception as exc:  # pragma: no cover - 环境缺失路径
        pytest.skip(f"agent_memory PostgreSQL 不可达，跳过 close 跨租户验收: {exc}")
    if not tables or any(table is None for table in tables):
        pytest.skip("客服 PostgreSQL 表未完成迁移，跳过 close 跨租户验收")


@pytest.fixture(autouse=True)
def _require_pg():
    _require_cs_pg()


def _jwt_request(tenant_id: str, user_id: str) -> Request:
    """构造网关注入身份头的请求（header 模式）。"""
    headers = {
        AUTH_TYPE_HEADER: "jwt",
        USER_ID_HEADER: user_id,
        USER_ROLES_HEADER: "agent",
        TENANT_ID_HEADER: tenant_id,
    }
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/cs/conversations/x/close",
            "query_string": b"",
            "headers": [
                (name.lower().encode(), value.encode())
                for name, value in headers.items()
            ],
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


async def _seed(tenant_id: str, suffix: str) -> dict[str, str]:
    """建立一个租户的启用坐席 + waiting_human 会话/工单。"""
    from backend.memory.database import AsyncSessionLocal

    agent_id = f"agent-{tenant_id}-{suffix}"
    conversation_id = f"conv-{tenant_id}-{suffix}"
    user_id = f"cust-{tenant_id}-{suffix}"
    async with AsyncSessionLocal() as db, db.begin():
        db.add(
            CSAgent(
                agent_id=agent_id,
                tenant_id=tenant_id,
                display_name=f"{tenant_id} 坐席",
                role="agent",
                auth_user_id=f"user-{tenant_id}-{suffix}",
                enabled=True,
                available=True,
            )
        )
        db.add(
            CSConversation(
                conversation_id=conversation_id,
                user_id=user_id,
                tenant_id=tenant_id,
                handling_mode="waiting_human",
            )
        )
        db.add(
            CSHandoff(
                handoff_id=f"h-{tenant_id}-{suffix}",
                conversation_id=conversation_id,
                user_id=user_id,
                tenant_id=tenant_id,
                handoff_state="waiting_human",
            )
        )
    return {
        "agent_id": agent_id,
        "conversation_id": conversation_id,
        "user_id": user_id,
    }


async def _handoff_state(conversation_id: str) -> str:
    from backend.memory.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        return (
            await db.execute(
                select(CSHandoff.handoff_state).where(
                    CSHandoff.conversation_id == conversation_id
                )
            )
        ).scalar_one()


async def _cleanup(seeds: list[dict[str, str]]) -> None:
    from backend.memory.database import AsyncSessionLocal

    conversation_ids = [s["conversation_id"] for s in seeds]
    agent_ids = [s["agent_id"] for s in seeds]
    async with AsyncSessionLocal() as db, db.begin():
        await db.execute(
            delete(CSEvent).where(CSEvent.conversation_id.in_(conversation_ids))
        )
        await db.execute(
            delete(CSAssignment).where(
                CSAssignment.conversation_id.in_(conversation_ids)
            )
        )
        await db.execute(
            delete(CSHandoff).where(
                CSHandoff.conversation_id.in_(conversation_ids)
            )
        )
        await db.execute(
            delete(CSConversation).where(
                CSConversation.conversation_id.in_(conversation_ids)
            )
        )
        await db.execute(delete(CSAgent).where(CSAgent.agent_id.in_(agent_ids)))


async def test_close_is_tenant_scoped() -> None:
    """他租坐席 close → 404 且工单不变；本租坐席 close → 成功。"""
    suffix = uuid.uuid4().hex[:8]
    tenant_a = await _seed("tenant-a", suffix)
    tenant_b = await _seed("tenant-b", suffix)
    try:
        # tenant-a 坐席关闭 tenant-b 的会话：必须 404，且不能产生任何写入
        with pytest.raises(HTTPException) as denied:
            await cs_admin.close_conversation(
                tenant_b["conversation_id"],
                cs_admin.ClaimRequest(agent_id=""),
                _jwt_request("tenant-a", f"user-tenant-a-{suffix}"),
            )
        assert denied.value.status_code == 404
        assert await _handoff_state(tenant_b["conversation_id"]) == "waiting_human"

        # 本租坐席关闭自己的会话：成功，状态落 closed
        result = await cs_admin.close_conversation(
            tenant_b["conversation_id"],
            cs_admin.ClaimRequest(agent_id=""),
            _jwt_request("tenant-b", f"user-tenant-b-{suffix}"),
        )
        assert result["handoff_state"] == "closed"
        assert await _handoff_state(tenant_b["conversation_id"]) == "closed"
    finally:
        await _cleanup([tenant_a, tenant_b])
