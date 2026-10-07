"""画像端点（GET /memory/profile）回归：MemoryRepository.list_active_profile + MemoryService.get_profile。

覆盖：
- eligibility 口径与召回一致：is_active + (tenant, user) 双维度 + 未过期
- 跨用户不可见、过期行不返回、非 default 租户不串
- 排序 = importance DESC，limit 生效
- get_profile 契约：records 结构字段 + 故障时 error 字段（不抛 500）
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg2
import pytest

from backend.config.database import MEMORY_DB_CONFIG
from backend.memory.database import AsyncSessionLocal
from backend.memory.models.memory import MemoryRecord
from backend.memory.repository.memory_repo import MemoryRepository
from backend.memory.service import MemoryService

pytestmark = pytest.mark.asyncio

_PREFIX = "profile-test-"


def _unique_user(tag: str) -> str:
    """每测试独立 user：真库不清理，防跨测试数据累积。"""
    return f"{_PREFIX}{tag}-{uuid4().hex[:8]}"
_TENANT = "default"


def _require_pg() -> None:
    try:
        with psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=2):
            pass
    except Exception as exc:
        pytest.skip(f"agent_memory PostgreSQL 不可达，跳过画像真实验收: {exc}")


@pytest.fixture(autouse=True)
def _real_pg_and_cleanup():
    _require_pg()
    yield


def _record(user_id: str, **overrides) -> MemoryRecord:
    fields = dict(
        id=uuid4(),
        tenant_id=_TENANT,
        user_id=user_id,
        session_id=f"{_PREFIX}session",
        memory_type="preference",
        content="测试画像条目",
        importance_score=0.7,
        confidence_score=0.9,
        origin="explicit",
        is_active=True,
    )
    fields.update(overrides)
    return MemoryRecord(**fields)


async def test_profile_eligibility_and_ordering():
    user, other = _unique_user('main'), _unique_user('other')
    async with AsyncSessionLocal() as db:
        repo = MemoryRepository(db)
        # 清场 + 造数：本人 3 条（含 1 过期）+ 他人 1 条 + 隔离租户 1 条 + 非 active 1 条
        rows = [
            _record(user, content="重要偏好", importance_score=0.9),
            _record(user, content="一般偏好", importance_score=0.6),
            _record(user, content="已过期", importance_score=0.99,
                    expire_at=datetime.now(timezone.utc) - timedelta(hours=1)),
            _record(other, content="别人的画像", importance_score=0.95),
            _record(user, tenant_id="quarantine", content="隔离租户画像", importance_score=0.98),
            _record(user, content="已失效", importance_score=0.99, is_active=False),
        ]
        for r in rows:
            await repo.insert(r)
        await db.commit()

        # rollback 会 expire ORM 实例，session 内先物化成纯数据
        contents = [r.content for r in await repo.list_active_profile(
            user_id=user, tenant_id=_TENANT)]

    assert contents == ["重要偏好", "一般偏好"], f"eligibility/排序不符: {contents}"


async def test_profile_limit():
    user = _unique_user('limit')
    async with AsyncSessionLocal() as db:
        repo = MemoryRepository(db)
        for i in range(5):
            await repo.insert(_record(user, content=f"条目{i}", importance_score=0.6 + i * 0.01))
        await db.commit()

        got = await repo.list_active_profile(user_id=user, tenant_id=_TENANT, limit=3)
        scores = [float(r.importance_score) for r in got]

    assert len(scores) == 3
    assert scores == sorted(scores, reverse=True)


async def test_service_get_profile_contract():
    result = await MemoryService().get_profile(user_id=_unique_user("svc"), tenant_id=_TENANT)
    assert "error" not in result
    assert isinstance(result["records"], list)
    for item in result["records"]:
        assert {"id", "memory_type", "content", "created_at"} <= set(item)
    # 按 importance DESC 全序
    scores = [r["importance_score"] for r in result["records"]]
    assert scores == sorted(scores, reverse=True)
