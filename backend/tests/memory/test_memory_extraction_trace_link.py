"""D9 trace 链路回归：end_turn 落 source_trace_id + worker 预算归因。

修复前：extraction job 与产生它的请求 trace 无任何持久化关联（记忆条目
→ evidence → 会话消息可回溯，但 trace 维度断裂）；抽取 LLM 调用的预算
归因依赖 2026-10-28 修复（scoped_request_budget("memory-extract:<job_id>",
user_id=来源用户)），本文件一并锁定该行为不回退。
"""
from __future__ import annotations

import uuid

import pytest

from backend.tests.memory.conftest import require_memory_pg


@pytest.fixture()
def _pg_user():
    require_memory_pg()
    user = f"pt-trace-{uuid.uuid4().hex[:10]}"
    tenant = "tenant-pt-trace"
    yield user, tenant
    import psycopg2

    from backend.config.database import MEMORY_DB_CONFIG

    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM memory_extraction_jobs WHERE user_id LIKE %s",
                           (f"{user}%",))
            cursor.execute("DELETE FROM memory_turn_idempotency WHERE user_id LIKE %s",
                           (f"{user}%",))
            cursor.execute("DELETE FROM chat_messages WHERE session_id LIKE %s",
                           (f"{user}%",))
            cursor.execute("DELETE FROM chat_sessions WHERE session_id LIKE %s",
                           (f"{user}%",))
        conn.commit()


class TestExtractionTraceLink:
    def test_end_turn_persists_source_trace_id(self, _pg_user):
        """end_turn 带 trace_id → memory_extraction_jobs.source_trace_id 落库。"""
        import asyncio

        from backend.memory.manager import memory_manager

        user, tenant = _pg_user
        session = f"trace-link-{uuid.uuid4().hex[:8]}"
        trace_id = "trace-abc123def456"
        loop = memory_manager._loop
        assert loop is not None and loop.is_running()

        memory_manager.end_turn(
            session, f"tracer 问题 {user}", f"tracer 回答 {user}",
            user_id=user, tenant_id=tenant, product="agent", trace_id=trace_id,
        )

        async def _read():
            from sqlalchemy import select

            from backend.memory.database import AsyncSessionLocal
            from backend.memory.models.memory import MemoryExtractionJob

            async with AsyncSessionLocal() as db:
                rows = (await db.execute(
                    select(MemoryExtractionJob).where(
                        MemoryExtractionJob.tenant_id == tenant,
                        MemoryExtractionJob.user_id == user,
                        MemoryExtractionJob.session_id == session,
                    )
                )).scalars().all()
                return [(r.product, r.source_trace_id) for r in rows]

        rows = asyncio.run_coroutine_threadsafe(_read(), loop).result(timeout=15)
        assert rows, "end_turn 应创建 extraction job"
        assert all(p == "agent" for p, _ in rows), rows
        assert any(t == trace_id for _, t in rows), rows

    def test_end_turn_without_trace_keeps_null(self, _pg_user):
        """无 trace 调用（旧调用方）→ source_trace_id 保持 NULL，行为兼容。"""
        import asyncio

        from backend.memory.manager import memory_manager

        user, tenant = _pg_user
        session = f"no-trace-{uuid.uuid4().hex[:8]}"
        loop = memory_manager._loop

        memory_manager.end_turn(
            session, f"问题 {user}", f"回答 {user}",
            user_id=user, tenant_id=tenant, product="agent",
        )

        async def _read():
            from sqlalchemy import select

            from backend.memory.database import AsyncSessionLocal
            from backend.memory.models.memory import MemoryExtractionJob

            async with AsyncSessionLocal() as db:
                rows = (await db.execute(
                    select(MemoryExtractionJob).where(
                        MemoryExtractionJob.session_id == session,
                        MemoryExtractionJob.product == "agent",
                    )
                )).scalars().all()
                return [r.source_trace_id for r in rows]

        rows = asyncio.run_coroutine_threadsafe(_read(), loop).result(timeout=15)
        assert rows and all(t is None for t in rows)


class TestWorkerBudgetAttribution:
    def test_worker_scopes_budget_to_source_identity(self):
        """worker 预算归因不回退：request_id=memory-extract:<job_id>、
        身份=来源用户（2026-10-28 计费审计行为，源码级锁定）。"""
        import inspect

        import backend.tasks.memory_extraction_tasks as mod

        src = inspect.getsource(mod.process_memory_extraction)
        assert "memory-extract:" in src
        assert 'scoped_request_budget(' in src
        assert 'user_id=payload["user_id"]' in src
        assert 'tenant_id=payload["tenant_id"]' in src
        # product/trace 载荷透传（ADR-0012 第 1 期）
        assert 'product=payload["product"]' in src
        assert 'job.product' in inspect.getsource(mod._claim_job)
        assert "job.source_trace_id" in inspect.getsource(mod._claim_job)
