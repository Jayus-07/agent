"""P3 PostgreSQL 并发验收：确认认领与人工会话认领。"""
from __future__ import annotations

import asyncio
import uuid

import psycopg2
import pytest
from fastapi import HTTPException
from sqlalchemy import text

from backend.config.database import MEMORY_DB_CONFIG


pytestmark = pytest.mark.asyncio


def _require_customer_service_pg() -> None:
    """PG 不可达或客服表未迁移时跳过，禁止用 SQLite 冒充并发验收。"""
    try:
        with psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=2) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        to_regclass('customer_service.confirmations'),
                        to_regclass('customer_service.handoffs'),
                        to_regclass('customer_service.conversations'),
                        to_regclass('customer_service.cs_agents'),
                        to_regclass('customer_service.assignments')
                    """
                )
                tables = cursor.fetchone()
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM information_schema.columns
                    WHERE table_schema = 'customer_service'
                      AND table_name = 'conversations'
                      AND column_name IN ('rating', 'rating_comment', 'rated_at')
                    """
                )
                rating_columns = cursor.fetchone()[0]
    except Exception as exc:
        pytest.skip(f"agent_memory PostgreSQL 不可达，跳过 P3 并发验收: {exc}")
    if not tables or any(table is None for table in tables):
        pytest.skip("客服 PostgreSQL 表未完成迁移，跳过 P3 并发验收")
    if rating_columns != 3:
        pytest.skip(
            "当前 PostgreSQL 的 conversations 缺少 014_cs_rating 列，"
            "跳过依赖完整 CSConversation ORM 的 P3 认领验收"
        )


@pytest.fixture(autouse=True)
def _require_pg():
    _require_customer_service_pg()


async def _run_concurrently(count: int, operation):
    """让所有 worker 先就绪，再同时进入数据库操作。"""
    ready = 0
    ready_lock = asyncio.Lock()
    all_ready = asyncio.Event()

    async def worker(index: int):
        nonlocal ready
        async with ready_lock:
            ready += 1
            if ready == count:
                all_ready.set()
        await all_ready.wait()
        return await operation(index)

    return await asyncio.gather(*(worker(index) for index in range(count)))


async def _dispose_memory_engine() -> None:
    """在 pytest event loop 关闭前释放 asyncpg pool，避免 Proactor 告警。"""
    from backend.memory import database

    engine = database._engine
    if engine is not None:
        await engine.dispose()
        database._engine = None
        database._sessionmaker = None
        database._engine_loop = None


async def _cleanup_confirmation(conversation_id: str, confirmation_id: str) -> None:
    from backend.memory.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "DELETE FROM customer_service.confirmations "
                "WHERE confirmation_id = :confirmation_id"
            ),
            {"confirmation_id": confirmation_id},
        )
        await db.execute(
            text(
                "DELETE FROM customer_service.conversations "
                "WHERE conversation_id = :conversation_id"
            ),
            {"conversation_id": conversation_id},
        )
        await db.commit()


async def _cleanup_handoff(
    conversation_id: str,
    handoff_id: str,
    agent_ids: list[str],
) -> None:
    from backend.memory.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "DELETE FROM customer_service.handoffs "
                "WHERE handoff_id = :handoff_id"
            ),
            {"handoff_id": handoff_id},
        )
        await db.execute(
            text(
                "DELETE FROM customer_service.conversations "
                "WHERE conversation_id = :conversation_id"
            ),
            {"conversation_id": conversation_id},
        )
        for agent_id in agent_ids:
            await db.execute(
                text(
                    "DELETE FROM customer_service.cs_agents "
                    "WHERE agent_id = :agent_id"
                ),
                {"agent_id": agent_id},
            )
        await db.commit()


async def test_confirmation_claim_has_one_winner_under_twenty_concurrent_requests():
    """同一 pending confirmation 并发 20 次时只能有一个认领成功。"""
    from backend.customer_service.repository.confirmation_repo import (
        ConfirmationRepository,
    )
    from backend.memory.database import AsyncSessionLocal

    suffix = uuid.uuid4().hex
    user_id = f"p3-conf-user-{suffix[:20]}"
    conversation_id = f"p3-conf-conv-{suffix[:20]}"
    confirmation_id = f"p3-conf-{suffix[:24]}"

    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                """
                INSERT INTO customer_service.conversations
                    (conversation_id, user_id, conversation_status,
                     handling_mode, channel, priority, labels, ai_enabled)
                VALUES
                    (:conversation_id, :user_id, 'open', 'ai', 'web',
                     'medium', ARRAY[]::text[], TRUE)
                """
            ),
            {"conversation_id": conversation_id, "user_id": user_id},
        )
        await db.execute(
            text(
                """
                INSERT INTO customer_service.confirmations
                    (confirmation_id, conversation_id, user_id,
                     action_type, target_type, target_id, proposal,
                     state, expires_at)
                VALUES
                    (:confirmation_id, :conversation_id, :user_id,
                     'refund_request', 'order', 'p3-order',
                     CAST(:proposal AS jsonb), 'pending',
                     NOW() + INTERVAL '10 minutes')
                """
            ),
            {
                "confirmation_id": confirmation_id,
                "conversation_id": conversation_id,
                "user_id": user_id,
                "proposal": '{"action_id": "' + confirmation_id + '"}',
            },
        )
        await db.commit()

    async def claim(_index: int):
        async with AsyncSessionLocal() as db:
            repo = ConfirmationRepository(db)
            claimed = await repo.claim_pending(user_id, conversation_id)
            await db.commit()
            return claimed

    try:
        results = await _run_concurrently(20, claim)

        assert results.count(confirmation_id) == 1
        assert results.count(None) == 19

        async with AsyncSessionLocal() as db:
            state = await db.scalar(
                text(
                    "SELECT state FROM customer_service.confirmations "
                    "WHERE confirmation_id = :confirmation_id"
                ),
                {"confirmation_id": confirmation_id},
            )
        assert state == "confirmed"
    finally:
        try:
            await _cleanup_confirmation(conversation_id, confirmation_id)
        finally:
            await _dispose_memory_engine()


async def test_handoff_claim_has_one_success_under_twenty_concurrent_agents(
    monkeypatch,
):
    """同一 waiting_human 会话由 20 个坐席并发认领时只能一个成功。"""
    from backend.app.api.routes import cs_admin
    from backend.memory.database import AsyncSessionLocal

    class _NoopStore:
        def invalidate(self, _user_id: str, _conversation_id: str) -> None:
            return None

    class _NoopHub:
        def publish(self, _event_type: str, **_payload) -> None:
            return None

    monkeypatch.setattr(
        "backend.customer_service.handoff_store.get_handoff_store",
        lambda: _NoopStore(),
    )
    monkeypatch.setattr(
        "backend.customer_service.realtime.get_agent_hub",
        lambda: _NoopHub(),
    )

    suffix = uuid.uuid4().hex
    user_id = f"p3-ho-user-{suffix[:20]}"
    conversation_id = f"p3-ho-conv-{suffix[:20]}"
    handoff_id = f"p3-ho-{suffix[:24]}"
    agent_ids = [f"p3-agent-{suffix[:10]}-{index}" for index in range(20)]

    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                """
                INSERT INTO customer_service.conversations
                    (conversation_id, user_id, conversation_status,
                     handling_mode, channel, priority, labels, ai_enabled)
                VALUES
                    (:conversation_id, :user_id, 'open', 'waiting_human',
                     'web', 'medium', ARRAY[]::text[], TRUE)
                """
            ),
            {"conversation_id": conversation_id, "user_id": user_id},
        )
        await db.execute(
            text(
                """
                INSERT INTO customer_service.handoffs
                    (handoff_id, conversation_id, user_id,
                     handoff_state, trigger_type, trigger_reason)
                VALUES
                    (:handoff_id, :conversation_id, :user_id,
                     'waiting_human', 'p3-test', 'concurrency')
                """
            ),
            {
                "handoff_id": handoff_id,
                "conversation_id": conversation_id,
                "user_id": user_id,
            },
        )
        await db.commit()

    async def claim(index: int):
        try:
            result = await cs_admin._async_claim(
                conversation_id, agent_ids[index], None
            )
            return ("ok", result)
        except HTTPException as exc:
            return ("http", exc.status_code, exc.detail)

    try:
        results = await _run_concurrently(20, claim)

        success = [
            result for result in results
            if result[0] == "ok" and not result[1]["already_claimed"]
        ]
        conflicts = [
            result for result in results
            if result[0] == "http" and result[1] == 409
        ]
        assert len(success) == 1
        assert len(conflicts) == 19

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                text(
                    "SELECT h.handoff_state, c.assigned_agent_id "
                    "FROM customer_service.handoffs h "
                    "JOIN customer_service.conversations c "
                    "  ON c.conversation_id = h.conversation_id "
                    "WHERE h.handoff_id = :handoff_id"
                ),
                {"handoff_id": handoff_id},
            )
            state, assigned_agent = result.one()
        assert state == "human_active"
        assert assigned_agent in agent_ids
    finally:
        try:
            await _cleanup_handoff(conversation_id, handoff_id, agent_ids)
        finally:
            await _dispose_memory_engine()
