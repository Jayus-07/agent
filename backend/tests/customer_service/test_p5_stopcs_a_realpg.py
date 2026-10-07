"""STOP CS-A 真 PostgreSQL 验收（G2/G4-PG 腿）。

前置：PGHOST/PGPORT/PGPASSWORD 指向真实 agent_memory（本机 5433）。
库不可达或客服表未迁移时 skip（禁止 SQLite 冒充）——与 p3 同守卫口径。

覆盖（审计 P0-2/P0-5/P0-6 的真库语义）：
  - 双租户数据落库与查询隔离（REST 仓储层谓词）
  - lifecycle.enter_waiting_handoff：同事务写 handoffs + conversations.handling_mode
    （审计 P0-6 的原始缺陷：expert 路径只写 handoffs）
  - migration 078：CHECK 约束拒绝幽灵状态（fail loud at DB 层）
  - human_active 离线自愈：真 PG + 真 Redis presence 全链
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.asyncio


def _require_customer_service_pg() -> None:
    try:
        import psycopg2

        from backend.config.database import MEMORY_DB_CONFIG

        with psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=3) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT to_regclass('customer_service.handoffs'),"
                    " to_regclass('customer_service.conversations')"
                )
                if cursor.fetchone()[0] is None:
                    pytest.skip("客服 PostgreSQL 表未完成迁移")
    except Exception as exc:
        pytest.skip(f"agent_memory PostgreSQL 不可达: {exc}")


@pytest.fixture(autouse=True)
def _require_pg():
    _require_customer_service_pg()


@pytest.fixture
def env():
    import psycopg2

    from backend.config.database import MEMORY_DB_CONFIG

    import psycopg2.extras

    conn = psycopg2.connect(**MEMORY_DB_CONFIG)
    yield conn
    conn.close()


def _run(coro):
    import asyncio

    return asyncio.run(coro)


_TENANT_A = "stopcs-a"
_TENANT_B = "stopcs-b"


def _cleanup(env: "Conn", tenants: tuple[str, ...]) -> None:
    cur = env.cursor()
    for tenant in tenants:
        cur.execute(
            "DELETE FROM customer_service.handoffs WHERE tenant_id = %s", (tenant,)
        )
        cur.execute(
            "DELETE FROM customer_service.conversations WHERE tenant_id = %s",
            (tenant,),
        )
        cur.execute(
            "DELETE FROM customer_service.cs_agents WHERE tenant_id = %s", (tenant,)
        )
    env.commit()


class TestDualTenantRealPG:
    """G4-PG：tenant_a / tenant_b 数据互相不可见。"""

    def test_tenant_isolation_on_real_pg(self, env):
        cur = env.cursor()
        suffix = uuid.uuid4().hex[:8]
        conv_a = f"stpa-{suffix}-a"
        conv_b = f"stpa-{suffix}-b"
        try:
            for conv, tenant, user in (
                (conv_a, _TENANT_A, "user-a"),
                (conv_b, _TENANT_B, "user-b"),
            ):
                cur.execute(
                    """
                    INSERT INTO customer_service.conversations
                        (conversation_id, user_id, tenant_id, conversation_status,
                         handling_mode, priority, labels)
                    VALUES (%s, %s, %s, 'open', 'ai', 'medium', '{}')
                    """,
                    (conv, user, tenant),
                )
            env.commit()

            from sqlalchemy import select

            from backend.customer_service.models.conversation import CSConversation
            from backend.memory.database import AsyncSessionLocal

            async def _queries():
                async with AsyncSessionLocal() as db:
                    rows_a = (
                        await db.execute(
                            select(CSConversation).where(
                                CSConversation.tenant_id == _TENANT_A,
                                CSConversation.conversation_id.in_([conv_a, conv_b]),
                            )
                        )
                    ).scalars().all()
                    rows_b = (
                        await db.execute(
                            select(CSConversation).where(
                                CSConversation.tenant_id == _TENANT_B,
                                CSConversation.conversation_id.in_([conv_a, conv_b]),
                            )
                        )
                    ).scalars().all()
                    return rows_a, rows_b

            rows_a, rows_b = _run(_queries())
            assert [r.conversation_id for r in rows_a] == [conv_a]
            assert [r.conversation_id for r in rows_b] == [conv_b]
        finally:
            _cleanup(env, (_TENANT_A, _TENANT_B))


class TestLifecycleRealPG:
    """P0-6：lifecycle 唯一入口同事务维护 handoffs + handling_mode。"""

    def test_enter_waiting_handoff_writes_both_tables(self, env):
        suffix = uuid.uuid4().hex[:8]
        conv_id = f"stpa-{suffix}"
        try:
            from backend.customer_service._db_loop import run_sync
            from backend.customer_service.handoff.lifecycle import (
                enter_waiting_handoff_sync,
            )

            row = enter_waiting_handoff_sync(
                tenant_id=_TENANT_A,
                conversation_id=conv_id,
                user_id="user-a",
                trigger_type="explicit_request",
                trigger_reason="STOP CS-A real PG",
                ticket_id=f"T-{suffix}",
            )
            assert row["handoff_state"] == "waiting_human"

            cur = env.cursor()
            cur.execute(
                "SELECT handoff_state, total_deadline_at IS NOT NULL, assigned_agent_id"
                " FROM customer_service.handoffs WHERE tenant_id=%s AND conversation_id=%s",
                (_TENANT_A, conv_id),
            )
            state, has_deadline, _assigned = cur.fetchone()
            assert state == "waiting_human"
            assert has_deadline is True  # 入池即带总期限（reaper 兜底可达）

            cur.execute(
                "SELECT handling_mode FROM customer_service.conversations"
                " WHERE tenant_id=%s AND conversation_id=%s",
                (_TENANT_A, conv_id),
            )
            # 原始缺陷回归断言：会话投影同事务写入（此前 expert 路径漏写）
            assert cur.fetchone()[0] == "waiting_human"
        finally:
            _cleanup(env, (_TENANT_A,))

    def test_ghost_state_rejected_by_check_constraint(self, env):
        """migration 078：initiated 等非法状态在 DB 层被拒绝（fail loud）。"""
        cur = env.cursor()
        suffix = uuid.uuid4().hex[:8]
        conv_id = f"stpa-{suffix}"
        cur.execute(
            """
            INSERT INTO customer_service.conversations
                (conversation_id, user_id, tenant_id, conversation_status,
                 handling_mode, priority, labels)
            VALUES (%s, 'user-a', %s, 'open', 'ai', 'medium', '{}')
            """,
            (conv_id, _TENANT_A),
        )
        with pytest.raises(Exception):
            cur.execute(
                """
                INSERT INTO customer_service.handoffs
                    (handoff_id, conversation_id, user_id, tenant_id, handoff_state)
                VALUES (%s, %s, 'user-a', %s, 'initiated')
                """,
                (f"h-{suffix}", conv_id, _TENANT_A),
            )
        env.rollback()


class TestHumanActiveRecoveryRealPG:
    """P0-5：真 PG + 真 Redis presence 的自愈全链。"""

    def test_offline_agent_recovered(self, env, monkeypatch):
        suffix = uuid.uuid4().hex[:8]
        conv_id = f"stpa-{suffix}"
        handoff_id = f"h-{suffix}"
        agent_id = f"agent-gone-{suffix}"
        stale = datetime.now(timezone.utc) - timedelta(seconds=600)
        cur = env.cursor()
        try:
            # handoffs.assigned_agent_id 有 (tenant, agent) 复合 FK —— 先建档
            cur.execute(
                """
                INSERT INTO customer_service.cs_agents
                    (agent_id, tenant_id, display_name, role, enabled,
                     available, accepting, skill, max_conversations)
                VALUES (%s, %s, 'STOP CS-A 离线坐席', 'agent', true,
                        true, true, 'general', 5)
                ON CONFLICT (tenant_id, agent_id) DO NOTHING
                """,
                (agent_id, _TENANT_A),
            )
            cur.execute(
                """
                INSERT INTO customer_service.conversations
                    (conversation_id, user_id, tenant_id, conversation_status,
                     handling_mode, assigned_agent_id, priority, labels)
                VALUES (%s, 'user-a', %s, 'open', 'human', %s, 'medium', '{}')
                """,
                (conv_id, _TENANT_A, agent_id),
            )
            cur.execute(
                """
                INSERT INTO customer_service.handoffs
                    (handoff_id, conversation_id, user_id, tenant_id,
                     handoff_state, assigned_agent_id, assignment_version,
                     attempt_count, priority, created_at, updated_at)
                VALUES (%s, %s, 'user-a', %s, 'human_active', %s, 1, 1, 50,
                        %s, %s)
                """,
                (handoff_id, conv_id, _TENANT_A, agent_id, stale, stale),
            )
            env.commit()

            # 该坐席不写 presence → Redis 里天然离线（真实 fail-closed 判定）
            from backend.customer_service.handoff.dispatch import reaper

            async def _do_recovery():
                from backend.memory.database import AsyncSessionLocal

                async with AsyncSessionLocal() as session, session.begin():
                    return await reaper.reap_stale_human_active(
                        session,
                        now=datetime.now(timezone.utc),
                        limit=50,
                    )

            result = _run(_do_recovery())

            assert result.recovered >= 1  # 本用例工单必在恢复集内
            cur.execute(
                "SELECT handoff_state, assigned_agent_id, assignment_version,"
                " total_deadline_at IS NOT NULL"
                " FROM customer_service.handoffs WHERE handoff_id=%s",
                (handoff_id,),
            )
            state, assigned, version, has_deadline = cur.fetchone()
            assert state == "waiting_human"
            assert assigned is None
            assert version == 2  # 1 → 2
            assert has_deadline is True  # deadline 已顺延

            cur.execute(
                "SELECT handling_mode, assigned_agent_id FROM"
                " customer_service.conversations WHERE conversation_id=%s",
                (conv_id,),
            )
            mode, conv_assigned = cur.fetchone()
            assert mode == "waiting_human"  # 投影同事务回写
            assert conv_assigned is None

            cur.execute(
                "SELECT type FROM customer_service.events"
                " WHERE conversation_id=%s AND type='conversation.human_active_recovered'",
                (conv_id,),
            )
            assert cur.fetchone() is not None  # outbox 事件同事务落库
        finally:
            _cleanup(env, (_TENANT_A,))


class _SessionCtx:
    """reaper 需要的 AsyncSession 上下文（真库）。"""

    async def __aenter__(self):
        from backend.memory.database import AsyncSessionLocal

        self._session = AsyncSessionLocal()
        return await self._session.__aenter__()

    async def __aexit__(self, *args):
        return await self._session.__aexit__(*args)


def _session_ctx():
    return _SessionCtx()
