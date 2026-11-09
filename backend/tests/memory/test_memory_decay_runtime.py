"""STOP C：Memory decay 生命周期验收（C23，§51-56/85）。

STOP A 确认 run_decay 全仓零调用方（死代码）；STOP C 决策 = 正式接线
maintenance beat（memory.daily_decay，每日 04:30 UTC）。本文件验证：
任务/调度/路由注册 + explicit 豁免 + 只动 active + 实际衰减/归档效果。

外部依赖边界：真实 PostgreSQL；不经 broker 实调 task（asyncio.run 与
pytest-asyncio loop 冲突），注册面用静态断言。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from backend.memory.database import AsyncSessionLocal
from backend.memory.models.memory import MemoryRecord
from backend.memory.repository.memory_repo import MemoryRepository
from backend.memory.service import MemoryService
from backend.tests.memory.conftest import (
    cleanup_memory_prefix,
    require_memory_pg,
)

pytestmark = pytest.mark.asyncio

_PREFIX = "stopc-decay-"
_USER = f"{_PREFIX}user"


@pytest.fixture(autouse=True)
async def _env():
    require_memory_pg()
    await cleanup_memory_prefix(_PREFIX)
    yield
    await cleanup_memory_prefix(_PREFIX)


def _record(origin: str, importance: float, days_since_access: int) -> MemoryRecord:
    return MemoryRecord(
        tenant_id="default", user_id=_USER, session_id=f"{_PREFIX}s",
        memory_type="preference", content=f"decay-test {origin} {importance}",
        embedding=None, importance_score=importance, confidence_score=1.0,
        origin=origin,
        last_access_at=datetime.now(timezone.utc) - timedelta(days=days_since_access),
    )


def _profile_record(origin: str, importance: float, days_since_access: int,
                    *, product: str = "agent", memory_key: str | None = None,
                    content: str | None = None) -> MemoryRecord:
    return MemoryRecord(
        tenant_id="default", user_id=_USER, session_id=f"{_PREFIX}s",
        memory_type="preference", content=content or f"decay-test {origin} {importance}",
        embedding=None, importance_score=importance, confidence_score=1.0,
        origin=origin, product=product, memory_key=memory_key,
        last_access_at=datetime.now(timezone.utc) - timedelta(days=days_since_access),
    )


async def _row(content: str) -> tuple:
    async with AsyncSessionLocal() as db:
        return (await db.execute(text(
            "SELECT importance_score, is_active FROM memory_records "
            "WHERE user_id=:u AND content=:c"), {"u": _USER, "c": content})).first()


async def test_decay_task_registered_end_to_end():
    """§85：task registered + schedule registered + 路由登记（fail-closed 面）。

    celery include 仅在 worker 启动时加载，测试进程需显式 import 任务模块。
    """
    import backend.tasks.memory_maintenance_tasks  # noqa: F401  触发 @task 注册
    from backend.tasks.celery_app import celery_app
    from backend.tasks.queue_router import beat_queue

    assert "memory.daily_decay" in celery_app.tasks
    entry = celery_app.conf.beat_schedule.get("memory-daily-decay")
    assert entry is not None and entry["task"] == "memory.daily_decay"
    assert beat_queue("memory.daily_decay") == entry["options"]["queue"]


async def test_decay_updates_inferred_only_and_spares_explicit():
    """§55：explicit 完全豁免；inferred 200 天 → ×0.9；只动 active（§54）。

    D4 边界（第 2 期 D-1 裁决）：衰减只降权，**任何行不得被归档**
    （原「importance<0.2 归档」废止——低分行保持 active，设置页可见）。
    """
    async with AsyncSessionLocal() as db:
        repo = MemoryRepository(db)
        for r in (
            _record("inferred", 0.5, 200),   # 命中 >180 档 ×0.9
            _record("explicit", 0.5, 200),   # 豁免
            _record("inferred", 0.15, 10),   # 低分 + 未到期 → 不动、不归档
            _record("inferred", 0.6, 120),   # 90~180 区间 → ×0.95（互斥）
            _record("inferred", 0.8, 1),     # 最近访问 → 不动
        ):
            await repo.insert(r)
        await db.commit()

    result = await MemoryService().run_decay()
    assert result["decayed"] >= 1
    assert "archived" not in result  # D4：衰减不再产出归档动作

    decayed = await _row("decay-test inferred 0.5")
    assert abs(decayed[0] - 0.45) < 1e-9 and decayed[1] is True
    explicit = await _row("decay-test explicit 0.5")
    assert explicit[0] == 0.5 and explicit[1] is True            # explicit 不衰减
    low = await _row("decay-test inferred 0.15")
    assert low[0] == 0.15 and low[1] is True                     # 未到期：不动且**不归档**
    midband = await _row("decay-test inferred 0.6")
    assert abs(midband[0] - 0.57) < 1e-9 and midband[1] is True  # 仅 ×0.95（不叠加 ×0.9）
    recent = await _row("decay-test inferred 0.8")
    assert abs(recent[0] - 0.8) < 1e-9 and recent[1] is True      # 未到期不动


async def test_decay_never_deactivates_any_row():
    """D4 边界总锁面：衰减循环后全部行保持 is_active=True（不删除不隐藏）。"""
    async with AsyncSessionLocal() as db:
        repo = MemoryRepository(db)
        for r in (
            _record("inferred", 0.5, 400),    # >180 档 ×0.9
            _record("legacy", 0.1, 200),      # 低分旧行：旧契约会被归档
            _record("inferred", 0.05, 150),   # 极低分：只降权
        ):
            await repo.insert(r)
        await db.commit()

    await MemoryService().run_decay()

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text(
            "SELECT is_active FROM memory_records WHERE user_id=:u"),
            {"u": _USER})).all()
    assert rows and all(r[0] is True for r in rows), "衰减不得使任何行失效"


async def test_decay_product_profiles_d5():
    """D5 档位：travel 快衰减（60 天 ×0.9），agent 慢（60 天不动）。"""
    async with AsyncSessionLocal() as db:
        repo = MemoryRepository(db)
        for r in (
            _profile_record("inferred", 0.5, 45, product="travel",
                            content="decay-test travel 45d"),   # [30,60) 下档 ×0.95
            _profile_record("inferred", 0.5, 45, product="agent",
                            content="decay-test agent 45d"),    # agent <90 不动
            _profile_record("inferred", 0.5, 70, product="travel",
                            content="decay-test travel 70d"),   # >60 上档 ×0.9
        ):
            await repo.insert(r)
        await db.commit()

    result = await MemoryService().run_decay()
    assert result["by_product"]["travel"] >= 2

    travel45 = await _row("decay-test travel 45d")
    assert abs(travel45[0] - 0.475) < 1e-9, "travel 45 天应命中下档 ×0.95"
    travel70 = await _row("decay-test travel 70d")
    assert abs(travel70[0] - 0.45) < 1e-9, "travel 70 天应命中上档 ×0.9"
    agent45 = await _row("decay-test agent 45d")
    assert agent45[0] == 0.5, "agent 档 45 天不应衰减（下档 90 天起）"


async def test_decay_safety_exempt_d2():
    """D-2 安全豁免：keyed 白名单 + 内容关键词行不衰减；普通行照常。"""
    async with AsyncSessionLocal() as db:
        repo = MemoryRepository(db)
        for r in (
            _profile_record("inferred", 0.5, 200, product="agent",
                            memory_key="health.allergy.peanut",
                            content="decay-test keyed safety"),
            _profile_record("inferred", 0.5, 200, product="agent",
                            content="decay-test 用户对花生过敏"),
            _profile_record("inferred", 0.5, 200, product="agent",
                            content="decay-test plain unkeyed"),
        ):
            await repo.insert(r)
        await db.commit()

    result = await MemoryService().run_decay()
    assert result["decayed"] >= 1

    keyed = await _row("decay-test keyed safety")
    assert keyed[0] == 0.5, "keyed 安全行不得衰减"
    content_safe = await _row("decay-test 用户对花生过敏")
    assert content_safe[0] == 0.5, "内容命中安全关键词的行不得衰减"
    plain = await _row("decay-test plain unkeyed")
    assert plain[0] < 0.5, "普通行应照常衰减"
