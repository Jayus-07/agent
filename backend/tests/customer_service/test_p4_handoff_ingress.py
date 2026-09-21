"""P4 用户入池服务的真实 PostgreSQL 事务与并发验收。"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import psycopg2
import pytest
from sqlalchemy import text

from backend.config.customer_service import CS_HANDOFF_TIMEOUT_SECONDS
from backend.config.database import MEMORY_DB_CONFIG
from backend.customer_service.dispatch.service import (
    ConversationForbidden,
    ConversationNotFound,
    create_or_reuse_handoff,
)
from backend.memory.database import AsyncSessionLocal

pytestmark = pytest.mark.asyncio


def _require_customer_service_pg() -> None:
    """PG 不可达或 P4 所需客服表未迁移时显式跳过。"""
    try:
        with psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=2) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        to_regclass('customer_service.conversations'),
                        to_regclass('customer_service.handoffs')
                    """
                )
                tables = cursor.fetchone()
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM information_schema.columns
                    WHERE table_schema = 'customer_service'
                      AND table_name = 'conversations'
                      AND column_name IN ('tenant_id', 'handling_mode')
                    """
                )
                conversation_columns = cursor.fetchone()[0]
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM information_schema.columns
                    WHERE table_schema = 'customer_service'
                      AND table_name = 'handoffs'
                      AND column_name IN (
                          'tenant_id', 'handoff_state', 'trigger_type',
                          'priority', 'idempotency_key', 'total_deadline_at'
                      )
                    """
                )
                handoff_columns = cursor.fetchone()[0]
    except Exception as exc:
        pytest.skip(f"agent_memory PostgreSQL 不可达，跳过 P4 真实验收: {exc}")
    if not tables or any(table is None for table in tables):
        pytest.skip("客服 PostgreSQL 表未完成迁移，跳过 P4 真实验收")
    if conversation_columns != 2 or handoff_columns != 6:
        pytest.skip("当前 PostgreSQL 缺少 P4 入池字段，跳过真实验收")


@pytest.fixture(autouse=True)
def _require_pg():
    _require_customer_service_pg()


async def _dispose_memory_engine() -> None:
    """在 pytest event loop 关闭前释放 asyncpg pool。"""
    from backend.memory import database

    engine = database._engine
    if engine is not None:
        await engine.dispose()
        database._engine = None
        database._sessionmaker = None
        database._engine_loop = None


async def _seed_conversation(
    conversation_id: str,
    *,
    user_id: str = "user-a",
    tenant_id: str = "tenant-a",
) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                """
                INSERT INTO customer_service.conversations
                    (conversation_id, user_id, tenant_id,
                     conversation_status, handling_mode, channel,
                     priority, labels, ai_enabled)
                VALUES
                    (:conversation_id, :user_id, :tenant_id,
                     'open', 'ai', 'web',
                     'medium', ARRAY[]::text[], TRUE)
                """
            ),
            {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "tenant_id": tenant_id,
            },
        )
        await db.commit()


async def _cleanup_conversation(conversation_id: str) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "DELETE FROM customer_service.handoffs "
                "WHERE conversation_id = :conversation_id"
            ),
            {"conversation_id": conversation_id},
        )
        await db.execute(
            text(
                "DELETE FROM customer_service.conversations "
                "WHERE conversation_id = :conversation_id"
            ),
            {"conversation_id": conversation_id},
        )
        await db.commit()


async def _run_concurrently(count: int, operation):
    """让所有 worker 就绪后同时进入真实数据库操作。"""
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


async def test_create_updates_conversation_in_same_committed_transaction():
    suffix = uuid.uuid4().hex
    conversation_id = f"p4-create-{suffix[:24]}"
    await _seed_conversation(conversation_id)

    try:
        async with AsyncSessionLocal() as db:
            result = await create_or_reuse_handoff(
                db,
                conversation_id=conversation_id,
                user_id="user-a",
                tenant_id="tenant-a",
                idempotency_key="p4-create-key",
            )

        assert result.reused is False
        assert result.handoff_state == "waiting_human"
        assert result.conversation_id == conversation_id
        assert result.total_deadline_at is not None
        remaining = result.total_deadline_at - datetime.now(timezone.utc)
        assert timedelta(seconds=CS_HANDOFF_TIMEOUT_SECONDS - 5) <= remaining
        assert remaining <= timedelta(seconds=CS_HANDOFF_TIMEOUT_SECONDS + 5)

        async with AsyncSessionLocal() as db:
            row = (
                await db.execute(
                    text(
                        """
                        SELECT h.handoff_state, h.trigger_type, h.priority,
                               h.tenant_id, h.user_id, h.total_deadline_at,
                               c.handling_mode
                        FROM customer_service.handoffs h
                        JOIN customer_service.conversations c
                          ON c.tenant_id = h.tenant_id
                         AND c.conversation_id = h.conversation_id
                        WHERE h.conversation_id = :conversation_id
                        """
                    ),
                    {"conversation_id": conversation_id},
                )
            ).one()

        assert row.handoff_state == "waiting_human"
        assert row.trigger_type == "explicit_request"
        assert row.priority == 50
        assert row.tenant_id == "tenant-a"
        assert row.user_id == "user-a"
        assert row.total_deadline_at is not None
        assert row.handling_mode == "waiting_human"
    finally:
        try:
            await _cleanup_conversation(conversation_id)
        finally:
            await _dispose_memory_engine()


async def test_active_handoff_is_reused_without_creating_a_second_row():
    suffix = uuid.uuid4().hex
    conversation_id = f"p4-reuse-{suffix[:24]}"
    await _seed_conversation(conversation_id)

    try:
        async with AsyncSessionLocal() as db:
            first = await create_or_reuse_handoff(
                db,
                conversation_id=conversation_id,
                user_id="user-a",
                tenant_id="tenant-a",
                idempotency_key="p4-first-key",
            )
            second = await create_or_reuse_handoff(
                db,
                conversation_id=conversation_id,
                user_id="user-a",
                tenant_id="tenant-a",
                idempotency_key="p4-second-key",
            )

        assert first.reused is False
        assert second.reused is True
        assert second.handoff_id == first.handoff_id

        async with AsyncSessionLocal() as db:
            count = await db.scalar(
                text(
                    "SELECT COUNT(*) FROM customer_service.handoffs "
                    "WHERE tenant_id = :tenant_id "
                    "AND conversation_id = :conversation_id "
                    "AND handoff_state <> 'closed'"
                ),
                {"tenant_id": "tenant-a", "conversation_id": conversation_id},
            )
        assert count == 1
    finally:
        try:
            await _cleanup_conversation(conversation_id)
        finally:
            await _dispose_memory_engine()


async def test_wrong_user_or_tenant_is_forbidden_and_not_mutated():
    suffix = uuid.uuid4().hex
    conversation_id = f"p4-owner-{suffix[:24]}"
    await _seed_conversation(conversation_id)

    try:
        async with AsyncSessionLocal() as db:
            with pytest.raises(ConversationForbidden):
                await create_or_reuse_handoff(
                    db,
                    conversation_id=conversation_id,
                    user_id="user-b",
                    tenant_id="tenant-a",
                    idempotency_key="p4-user-b",
                )
            with pytest.raises(ConversationForbidden):
                await create_or_reuse_handoff(
                    db,
                    conversation_id=conversation_id,
                    user_id="user-a",
                    tenant_id="tenant-b",
                    idempotency_key="p4-tenant-b",
                )

        async with AsyncSessionLocal() as db:
            count = await db.scalar(
                text(
                    "SELECT COUNT(*) FROM customer_service.handoffs "
                    "WHERE conversation_id = :conversation_id"
                ),
                {"conversation_id": conversation_id},
            )
        assert count == 0
    finally:
        try:
            await _cleanup_conversation(conversation_id)
        finally:
            await _dispose_memory_engine()


async def test_missing_conversation_is_not_created():
    async with AsyncSessionLocal() as db:
        with pytest.raises(ConversationNotFound):
            await create_or_reuse_handoff(
                db,
                conversation_id=f"missing-{uuid.uuid4().hex[:16]}",
                user_id="user-a",
                tenant_id="tenant-a",
                idempotency_key="p4-missing",
            )
    await _dispose_memory_engine()


async def test_one_active_handoff_for_one_hundred_concurrent_requests():
    suffix = uuid.uuid4().hex
    conversation_id = f"p4-race-{suffix[:24]}"
    await _seed_conversation(conversation_id)

    async def request(index: int):
        async with AsyncSessionLocal() as db:
            return await create_or_reuse_handoff(
                db,
                conversation_id=conversation_id,
                user_id="user-a",
                tenant_id="tenant-a",
                idempotency_key=f"p4-race-key-{index}",
            )

    try:
        results = await _run_concurrently(100, request)

        assert sum(not result.reused for result in results) == 1
        assert len({result.handoff_id for result in results}) == 1

        async with AsyncSessionLocal() as db:
            active_count = await db.scalar(
                text(
                    "SELECT COUNT(*) FROM customer_service.handoffs "
                    "WHERE tenant_id = :tenant_id "
                    "AND conversation_id = :conversation_id "
                    "AND handoff_state <> 'closed'"
                ),
                {"tenant_id": "tenant-a", "conversation_id": conversation_id},
            )
            handling_mode = await db.scalar(
                text(
                    "SELECT handling_mode FROM customer_service.conversations "
                    "WHERE tenant_id = :tenant_id "
                    "AND conversation_id = :conversation_id"
                ),
                {"tenant_id": "tenant-a", "conversation_id": conversation_id},
            )

        assert active_count == 1
        assert handling_mode == "waiting_human"
    finally:
        try:
            await _cleanup_conversation(conversation_id)
        finally:
            await _dispose_memory_engine()
