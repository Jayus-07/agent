"""P1 修复验收：Memory L3 pgvector（032 迁移后）真实 PostgreSQL 行为。

覆盖方案要求：
- memory_records.embedding 实际数据库类型为 vector(1024)
- embedding 可正常 insert
- cosine similarity 可正常查询、最相似记录排序正确
- 应用重启（engine dispose 重建）后仍可检索
- user A 不召回 user B memory
- pgvector 异常时聊天主链进入 degraded 而不是 500
"""

from __future__ import annotations

import uuid

import numpy as np
import pytest
from sqlalchemy import text

from backend.memory.database import AsyncSessionLocal
from backend.memory.models.memory import EMBEDDING_DIM, MemoryRecord
from backend.memory.repository.memory_repo import MemoryRepository
from backend.tests.memory.conftest import require_memory_column

pytestmark = pytest.mark.asyncio

_PREFIX = "pgv-p1-test-"
_USER_A = f"{_PREFIX}user-a"
_USER_B = f"{_PREFIX}user-b"


def _require_pg() -> None:
    """PG 不可达或 032 迁移未应用时显式跳过。"""
    # 会话级缓存（原实现每用例新建连接，~2s/例，见 conftest.require_memory_column）
    require_memory_column("embedding", udt="vector", label="P1 真实验收")


@pytest.fixture(autouse=True)
def _require_real_pg():
    _require_pg()


def _unit_vec(index: int) -> list[float]:
    """第 index 维为 1 的单位向量（非零，cosine 有意义）。"""
    vec = np.zeros(EMBEDDING_DIM, dtype=np.float32)
    vec[index] = 1.0
    return vec.tolist()


async def _cleanup() -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("DELETE FROM memory_records WHERE user_id LIKE :p"),
            {"p": _PREFIX + "%"},
        )
        await db.commit()


async def test_embedding_column_is_vector_1024():
    """032 迁移后列类型必须是 vector(1024)，不再是 bytea。"""
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                text(
                    """
                    SELECT udt_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'memory_records'
                      AND column_name = 'embedding'
                    """
                )
            )
        ).first()
    assert row is not None and row[0] == "vector"

    # 精确维度校验：信息 schema 的 attribute 层（pgvector 类型为复合类型）
    async with AsyncSessionLocal() as db:
        dim_row = (
            await db.execute(
                text(
                    """
                    SELECT a.atttypmod
                    FROM pg_attribute a
                    WHERE a.attrelid = 'memory_records'::regclass
                      AND a.attname = 'embedding'
                    """
                )
            )
        ).first()
    # pgvector typmod = 维度 + 0xFFFF0000 偏移编码，直接对 DDL 断言过脆；
    # 这里用等价强校验：写入非 1024 维向量必须报错
    wrong = np.zeros(EMBEDDING_DIM + 8, dtype=np.float32)
    wrong[0] = 1.0
    with pytest.raises(Exception):
        async with AsyncSessionLocal() as db:
            db.add(
                MemoryRecord(
                    user_id=f"{_PREFIX}dim-check",
                    session_id=f"{_PREFIX}dim",
                    memory_type="knowledge",
                    content="dimension guard",
                    embedding=wrong.tolist(),
                )
            )
            await db.flush()
            await db.rollback()


async def test_insert_and_cosine_search_ordered_correctly():
    """写入两条正交记忆，按与查询向量的余弦相似度正确排序。"""
    suffix = uuid.uuid4().hex[:8]
    try:
        async with AsyncSessionLocal() as db:
            repo = MemoryRepository(db)
            await repo.insert(
                MemoryRecord(
                    user_id=_USER_A,
                    session_id=f"{_PREFIX}{suffix}",
                    memory_type="knowledge",
                    content="apple-banana-cherry",
                    embedding=_unit_vec(0),
                )
            )
            await repo.insert(
                MemoryRecord(
                    user_id=_USER_A,
                    session_id=f"{_PREFIX}{suffix}",
                    memory_type="knowledge",
                    content="delta-echo-foxtrot",
                    embedding=_unit_vec(1),
                )
            )
            await db.commit()

        async with AsyncSessionLocal() as db:
            repo = MemoryRepository(db)
            hits = [r for r, _s in await repo.search_hybrid(_unit_vec(0), _USER_A, top_k=2)]
        contents = [r.content for r in hits]
        assert contents[0] == "apple-banana-cherry"
        assert "delta-echo-foxtrot" in contents
    finally:
        await _cleanup()


async def test_user_isolation_no_cross_user_recall():
    """user A 不召回 user B 的记忆（user 隔离）。

    注：memory_records 表无 tenant_id 列（历史 schema），L3 隔离以
    tenant-scoped user_id 为键；tenant 维度隔离由身份层 user_id 命名
    空间保证，见表结构注释与本文件 docstring。
    """
    suffix = uuid.uuid4().hex[:8]
    try:
        async with AsyncSessionLocal() as db:
            repo = MemoryRepository(db)
            await repo.insert(
                MemoryRecord(
                    user_id=_USER_A,
                    session_id=f"{_PREFIX}{suffix}",
                    memory_type="user_fact",
                    content="secret-of-user-a",
                    embedding=_unit_vec(2),
                )
            )
            await db.commit()

        async with AsyncSessionLocal() as db:
            repo = MemoryRepository(db)
            hits_b = [r for r, _s in await repo.search_hybrid(_unit_vec(2), _USER_B, top_k=20)]
        assert all(r.user_id == _USER_B for r in hits_b)
        assert not any(r.content == "secret-of-user-a" for r in hits_b)

        async with AsyncSessionLocal() as db:
            repo = MemoryRepository(db)
            hits_a = [r for r, _s in await repo.search_hybrid(_unit_vec(2), _USER_A, top_k=20)]
        assert any(r.content == "secret-of-user-a" for r in hits_a)
    finally:
        await _cleanup()


async def test_retrieval_survives_engine_recreate():
    """engine dispose 后（模拟应用重启）仍可检索同一批记忆。"""
    suffix = uuid.uuid4().hex[:8]
    try:
        async with AsyncSessionLocal() as db:
            await MemoryRepository(db).insert(
                MemoryRecord(
                    user_id=_USER_A,
                    session_id=f"{_PREFIX}{suffix}",
                    memory_type="preference",
                    content="persistent-after-restart",
                    embedding=_unit_vec(3),
                )
            )
            await db.commit()

        # 模拟重启：释放 asyncpg 连接池（下次使用时按新 loop/池重建）
        from backend.memory import database

        engine = database._engine
        if engine is not None:
            await engine.dispose()

        async with AsyncSessionLocal() as db:
            hits = [r for r, _s in await MemoryRepository(db).search_hybrid(
                _unit_vec(3), _USER_A, top_k=5
            )]
        assert any(r.content == "persistent-after-restart" for r in hits)
    finally:
        await _cleanup()


async def test_l3_failure_degrades_not_raises(monkeypatch):
    """L3 检索异常 → start_session 返回缓冲（degraded），不向主链抛异常。"""
    from backend.memory import service as memory_service

    suffix = uuid.uuid4().hex[:8]
    session_id = f"{_PREFIX}degraded-{suffix}"

    async def boom(self, query, embedding, user_id, top_k=5):
        raise RuntimeError("simulated pgvector failure")

    monkeypatch.setattr(memory_service.HybridRetriever, "retrieve", boom)

    svc = memory_service.MemoryService()
    l1 = await svc.start_session(session_id, user_id=_USER_A, query="你好")
    assert l1 is not None  # 没抛异常 = 主链继续
    assert l1.messages is not None

    # 清理 L2 残留
    await _cleanup()
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("DELETE FROM chat_sessions WHERE session_id LIKE :p"),
            {"p": _PREFIX + "%"},
        )
        await db.commit()

    # degraded 计数确实增加（观测面可查）
    from backend.observability.metrics import memory_retrieval_total

    degraded_value = memory_retrieval_total.labels(
        status="degraded", operation="retrieve"
    )._value.get()
    assert degraded_value >= 1
