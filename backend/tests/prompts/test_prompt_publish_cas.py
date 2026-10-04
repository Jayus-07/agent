"""Prompt 发布 active_version CAS 语义回归（CON-09）。

真库集成：验证 set_active_version 的乐观锁 WHERE 语义——期望值匹配才更新、
过期期望匹配 0 行返回 False。无 PG 环境自动跳过（不造假绿）。
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from backend.memory.models.prompt import Prompt, PromptVersion

pytestmark = [pytest.mark.asyncio]


async def _engine_available() -> bool:
    try:
        from sqlalchemy import text

        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 — 无 DB 环境跳过
        return False


@pytest_asyncio.fixture
async def pg_session():
    if not await _engine_available():
        pytest.skip("PostgreSQL 不可达，跳过 CAS 真库回归")
    from backend.memory.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()


async def _seed_prompt(session, key: str) -> Prompt:
    prompt = Prompt(
        key=key, name="cas 回归", category="test", risk_level="low",
        active_version=None,
    )
    session.add(prompt)
    await session.flush()
    session.add(PromptVersion(
        prompt_id=prompt.id, version=1, template="v1 {{x}}",
        status="draft", created_by="cas-test",
    ))
    await session.flush()
    return prompt


async def test_cas_rejects_stale_expected_version(pg_session):
    from sqlalchemy import delete

    from backend.memory.repository.prompt_repo import PromptRepository

    key = f"cas.test.{uuid.uuid4().hex[:10]}"
    repo = PromptRepository(pg_session)
    prompt = await _seed_prompt(pg_session, key)

    # 期望 None（从未发布）→ 命中，激活 v1
    assert await repo.set_active_version(
        prompt.id, 1, expected_current_version=None,
    ) is True
    # 期望 1（同事务内读到）→ 命中，切到 v2
    assert await repo.set_active_version(
        prompt.id, 2, expected_current_version=1,
    ) is True
    # 过期期望 1（实际已是 2）→ 0 行，返回 False（并发冲突信号）
    assert await repo.set_active_version(
        prompt.id, 3, expected_current_version=1,
    ) is False
    # 不带 CAS 参数 → 保持旧语义无条件更新
    assert await repo.set_active_version(prompt.id, 3) is True

    await pg_session.execute(delete(Prompt).where(Prompt.key == key))
