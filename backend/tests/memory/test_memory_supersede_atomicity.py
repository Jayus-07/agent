"""STOP C 原子 supersede + 并发安全验收（C16/C17/C18，§68/69/71）。

外部依赖边界：真实 PostgreSQL；LLM/embedding mock。
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.memory.database import AsyncSessionLocal
from backend.memory.keying import StoreOutcome
from backend.memory.long_term import LongTermMemory, MemoryFact
from backend.memory.repository.memory_repo import MemoryRepository
from backend.tests.memory.conftest import (
    ScriptedEmbedding,
    cleanup_memory_prefix,
    require_memory_pg,
)

pytestmark = pytest.mark.asyncio

_PREFIX = "stopc-atom-"
_USER = f"{_PREFIX}user"
_SESSION = f"{_PREFIX}session"


@pytest.fixture(autouse=True)
async def _env():
    require_memory_pg()
    await cleanup_memory_prefix(_PREFIX)
    yield
    await cleanup_memory_prefix(_PREFIX)


def _fact(value: str) -> MemoryFact:
    return MemoryFact(fact_type="user_fact", content=f"主模型是 {value}",
                      memory_key="project.main_llm", structured_value=value,
                      origin="inferred", confidence_score=0.8)


async def _active_count(user: str = _USER) -> int:
    async with AsyncSessionLocal() as db:
        return (await db.execute(text(
            "SELECT count(*) FROM memory_records "
            "WHERE user_id=:u AND tenant_id='default' AND is_active "
            "AND memory_key='project.main_llm'"), {"u": user})).scalar()


async def test_b71_partial_unique_index_blocks_two_active():
    """§71：绕过应用层直接 DB 写两条 active keyed → 第二条必须 constraint failure。"""
    from backend.memory.models.memory import MemoryRecord

    def _raw(value: str) -> MemoryRecord:
        return MemoryRecord(
            tenant_id="default", user_id=_USER, session_id=_SESSION,
            memory_type="user_fact", content=f"raw {value}",
            embedding=None, importance_score=0.5, confidence_score=1.0,
            origin="inferred", memory_key="project.main_llm",
            structured_value=value,
        )

    async with AsyncSessionLocal() as db:
        db.add(_raw("deepseek"))
        await db.commit()
    with pytest.raises(IntegrityError):
        async with AsyncSessionLocal() as db:
            db.add(_raw("doubao"))
            await db.commit()
    assert await _active_count() == 1

    # 一个 active 一个 inactive 允许共存（版本链保留）
    async with AsyncSessionLocal() as db:
        await db.execute(text(
            "UPDATE memory_records SET is_active=false "
            "WHERE user_id=:u AND tenant_id='default' AND memory_key='project.main_llm'"),
            {"u": _USER})
        db.add(_raw("qwen"))
        await db.commit()
    assert await _active_count() == 1


async def test_b69_supersede_rollback_keeps_old_active():
    """§69/C17：supersede 中 insert 抛异常 → 同事务回滚 → 旧记录仍 active。"""
    async with AsyncSessionLocal() as db:
        l3 = LongTermMemory(MemoryRepository(db))
        l3._embedding_model = ScriptedEmbedding()
        r1 = await l3.store_with_resolution(_fact("deepseek"), _USER, _SESSION, "default")
        await db.commit()
    assert r1.outcome == StoreOutcome.INSERTED

    # 在同一事务里做 supersede，让 insert 步骤抛异常 → 整体回滚
    with pytest.raises(RuntimeError):
        async with AsyncSessionLocal() as db:
            repo = MemoryRepository(db)
            l3 = LongTermMemory(repo)
            l3._embedding_model = ScriptedEmbedding()

            async def boom(*args, **kwargs):
                raise RuntimeError("simulated insert failure")

            repo.insert = boom  # 仅注入本事务的 insert 失败
            await l3.store_with_resolution(_fact("doubao"), _USER, _SESSION, "default")
            # 未到 commit 即抛出 → with 退出触发隐式 rollback

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text(
            "SELECT is_active, structured_value FROM memory_records "
            "WHERE user_id=:u AND tenant_id='default' AND memory_key='project.main_llm'"),
            {"u": _USER})).all()
    assert len(rows) == 1 and rows[0][0] is True and rows[0][1] == "deepseek"  # 旧事实恢复


async def test_b68_concurrent_same_key_single_active():
    """§68/C18：并发写同 key 不同值 → 最终恰好一个 active（不要求谁赢）。"""
    emb1, emb2 = ScriptedEmbedding(), ScriptedEmbedding()

    async def _write(value: str, emb) -> None:
        async with AsyncSessionLocal() as db:
            repo = MemoryRepository(db)
            l3 = LongTermMemory(repo)
            l3._embedding_model = emb
            try:
                await l3.store_with_resolution(_fact(value), _USER, _SESSION, "default")
                await db.commit()
            except IntegrityError:
                # 与 service.store 相同的有界重试路径：rollback 后重裁决一次
                await db.rollback()
                l2 = LongTermMemory(MemoryRepository(db))
                l2._embedding_model = emb
                await l2.store_with_resolution(_fact(value), _USER, _SESSION, "default")
                await db.commit()

    await asyncio.gather(_write("doubao", emb1), _write("qwen", emb2))
    assert await _active_count() == 1
