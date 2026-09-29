"""test_case_service.py — 统一案件 cs_case 核心闭环（迁移 B12）

覆盖：状态机白名单 / SLA 计算 / 创建→分配→处理→关闭闭环（mock DB 会话）/
同会话同类型防重入的幂等复用（DB 决胜 IntegrityError 转译）/ 结案 resolution
必填。真实库联动由实机冒烟覆盖（投诉场景建案可查）。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.customer_service.case.service import CSCaseService
from backend.customer_service.errors import BusinessRuleError
from backend.customer_service.models.case import (
    CASE_ACTIVE_STATUSES,
    CASE_SLA_MINUTES,
    compute_sla_deadline,
)


# ── 纯逻辑：状态机与 SLA ─────────────────────────────────


class TestStateMachineAndSla:

    def test_sla_per_priority(self):
        from datetime import timedelta

        base = compute_sla_deadline("P0")
        p2 = compute_sla_deadline("P2")
        assert CASE_SLA_MINUTES == {"P0": 5, "P1": 30, "P2": 24 * 60}
        assert p2 - base >= timedelta(hours=23)

    def test_sla_unknown_priority_falls_to_p2(self):
        from datetime import timedelta

        assert (compute_sla_deadline("PX") - compute_sla_deadline("P2")) < timedelta(seconds=5)


# ── 闭环（mock DB 会话）──────────────────────────────────


class _FakeResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeDB:
    """支持 `async with AsyncSessionLocal() as db`；记录 commit/rollback。"""

    def __init__(self, obj=None, flush_error=None):
        self.obj = obj
        self.flush_error = flush_error
        self.commits = 0
        self.rollbacks = 0
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        if self.flush_error is not None:
            raise self.flush_error

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def execute(self, *_a, **_k):
        return _FakeResult(self.obj)


class _Case:
    """最小案件替身（ORM 属性面）。"""

    def __init__(self, status="open", case_id="c-1", first_responded_at=None):
        self.case_id = case_id
        self.tenant_id = "default"
        self.conversation_id = "conv-1"
        self.user_id = "u1"
        self.case_type = "complaint"
        self.priority = "P1"
        self.status = status
        self.title = ""
        self.context = {}
        self.owner_agent_id = None
        self.related_confirmation_id = None
        self.resolution = None
        self.sla_deadline_at = None
        self.first_responded_at = first_responded_at
        self.created_at = None
        self.updated_at = None


def _patch_session(db):
    return patch(
        "backend.memory.database.AsyncSessionLocal",
        return_value=db,
    )


@pytest.mark.asyncio
async def test_full_lifecycle_create_assign_process_close():
    """核心闭环：创建 → 分配 → 处理 → 关闭（resolution 必填）。"""
    svc = CSCaseService()
    created = _Case()
    db = _FakeDB()
    with _patch_session(db):
        result = await svc.async_create(
            conversation_id="conv-1", user_id="u1", case_type="complaint",
            priority="P1", title="投诉",
        )
    assert result["status"] == "open"
    assert result["priority"] == "P1"
    assert result["sla_deadline_at"] is not None

    case = _Case(status="open")
    with _patch_session(_FakeDB(obj=case)):
        assigned = await svc.async_assign("c-1", "agent-7")
    assert assigned["owner_agent_id"] == "agent-7"
    assert assigned["first_responded_at"] is not None  # 首响 SLA 打点

    with _patch_session(_FakeDB(obj=case)):
        processing = await svc.async_start_process("c-1")
    assert processing["status"] == "processing"

    with pytest.raises(BusinessRuleError):
        with _patch_session(_FakeDB(obj=case)):
            await svc.async_close("c-1", "")  # resolution 必填

    with _patch_session(_FakeDB(obj=case)):
        closed = await svc.async_close("c-1", "已补偿并致歉")
    assert closed["status"] == "closed"
    assert closed["resolution"] == "已补偿并致歉"


@pytest.mark.asyncio
async def test_illegal_transition_fail_fast():
    """closed 后不可再流转（白名单 fail-fast）。"""
    svc = CSCaseService()
    closed = _Case(status="closed")
    with _patch_session(_FakeDB(obj=closed)):
        with pytest.raises(BusinessRuleError):
            await svc.async_assign("c-1", "agent-7")


@pytest.mark.asyncio
async def test_create_idempotent_reuse_on_active_conflict():
    """同会话同类型活跃案件冲突：DB 唯一索引决胜 → 幂等复用赢家行。"""
    from sqlalchemy.exc import IntegrityError

    svc = CSCaseService()
    winner = _Case(case_id="c-winner")
    # 第一次 execute（flush 后重查）返回赢家行
    db = _FakeDB(obj=winner, flush_error=IntegrityError("uq", None, Exception()))
    with _patch_session(db):
        result = await svc.async_create(
            conversation_id="conv-1", user_id="u1", case_type="complaint",
        )
    assert result["case_id"] == "c-winner"
    assert db.rollbacks == 1
    assert db.commits == 0


@pytest.mark.asyncio
async def test_create_rejects_bad_type_and_priority():
    svc = CSCaseService()
    with pytest.raises(BusinessRuleError):
        await svc.async_create(
            conversation_id="conv-1", user_id="u1", case_type="bogus")
    with pytest.raises(BusinessRuleError):
        await svc.async_create(
            conversation_id="conv-1", user_id="u1", case_type="complaint",
            priority="P9")


def test_active_statuses_match_migration_predicate():
    """ORM 活跃状态集必须与 058 部分唯一索引谓词成对（两处同步改）。"""
    from pathlib import Path

    sql = (Path(__file__).resolve().parents[2]
           / "sql" / "migrations" / "058_cs_case.sql").read_text(encoding="utf-8")
    for s in CASE_ACTIVE_STATUSES:
        assert f"'{s}'" in sql
    assert "uq_cs_case_active" in sql
