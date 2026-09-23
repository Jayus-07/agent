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
    """§55：explicit 完全豁免；inferred 200 天 → ×0.9；只动 active（§54）。"""
    async with AsyncSessionLocal() as db:
        repo = MemoryRepository(db)
        for r in (
            _record("inferred", 0.5, 200),   # 命中 >180 档 ×0.9
            _record("explicit", 0.5, 200),   # 豁免
            _record("inferred", 0.15, 10),   # importance<0.2 → 归档
            _record("inferred", 0.6, 120),   # 90~180 区间 → ×0.95（互斥）
            _record("inferred", 0.8, 1),     # 最近访问 → 不动
        ):
            await repo.insert(r)
        await db.commit()

    result = await MemoryService().run_decay()
    assert result["decayed"] >= 1

    decayed = await _row("decay-test inferred 0.5")
    assert abs(decayed[0] - 0.45) < 1e-9 and decayed[1] is True
    explicit = await _row("decay-test explicit 0.5")
    assert explicit[0] == 0.5 and explicit[1] is True            # explicit 不衰减
    low = await _row("decay-test inferred 0.15")
    assert low[1] is False                                        # <0.2 归档
    midband = await _row("decay-test inferred 0.6")
    assert abs(midband[0] - 0.57) < 1e-9 and midband[1] is True  # 仅 ×0.95（不叠加 ×0.9）
    recent = await _row("decay-test inferred 0.8")
    assert abs(recent[0] - 0.8) < 1e-9 and recent[1] is True      # 未到期不动
