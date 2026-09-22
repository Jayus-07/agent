"""test_tickets.py — 统一工单（批次C）单元测试。

覆盖：
  - 状态机校验：合法/非法流转
  - store CRUD（mock DB 会话）
  - 投诉/转人工 expert 落库收编（mock create_sync）
  - query_expert 工单格式化
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.customer_service.errors import BusinessRuleError
from backend.customer_service.ticket_store import (
    TicketStore,
    new_ticket_id,
    validate_transition,
)


# ── 状态机 ───────────────────────────────────────────────


def test_transition_open_to_processing_ok():
    validate_transition("open", "processing")


def test_transition_illegal_reopen_from_closed():
    with pytest.raises(BusinessRuleError):
        validate_transition("closed", "processing")


def test_transition_illegal_resolved_to_pending_user():
    with pytest.raises(BusinessRuleError):
        validate_transition("resolved", "pending_user")


def test_new_ticket_id_prefix():
    tid = new_ticket_id()
    assert tid.startswith("TCK-") and len(tid) == 12


# ── store：async_create / async_transition（mock DB）──────


class _FakeSessionCtx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *args):
        return False


class _FakeSession:
    """同时支持 `async with AsyncSessionLocal() as db` 与 `async with db.begin()`。"""

    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    def begin(self):
        return _FakeSessionCtx(self)

    async def execute(self, *_a, **_k):
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        return result

    async def flush(self):
        pass

    def __call__(self):
        return _FakeSessionCtx(self)


@pytest.mark.asyncio
async def test_async_create_rejects_bad_type():
    with pytest.raises(BusinessRuleError):
        await TicketStore().async_create(
            ticket_id="TCK-X", conversation_id="c1", user_id="u1",
            type="bogus", status="open",
        )


@pytest.mark.asyncio
async def test_async_create_rejects_bad_status():
    with pytest.raises(BusinessRuleError):
        await TicketStore().async_create(
            ticket_id="TCK-X", conversation_id="c1", user_id="u1",
            type="complaint", status="bogus",
        )


# ── expert 收编：投诉工单落库 ────────────────────────────


def test_complaint_expert_persists_ticket():
    """投诉 expert 应调用 ticket_store.create_sync 落库。"""
    from backend.customer_service.experts import complaint as ce

    state = {
        "user_id": "u1",
        "session_id": "conv-1",
        "conversation_id": "conv-1",
        "cs_context": {},
    }
    captured = {}

    class _FakeStore:
        def create_sync(self, **fields):
            captured.update(fields)
            return {"ticket_id": fields.get("ticket_id", "")}

    with patch(
        "backend.customer_service.ticket_store.get_ticket_store",
        return_value=_FakeStore(),
    ), patch(
        "backend.customer_service.handoff_store.get_handoff_store",
    ) as _hs, patch(
        "backend.observability.metrics.record_cs_handoff",
    ), patch(
        "backend.customer_service.realtime.get_agent_hub",
    ) as _hub:
        result = ce.execute_complaint("我要投诉，态度太差了", state)

    assert result["status"] == "success"
    assert captured.get("type") == "complaint"
    assert captured.get("user_id") == "u1"
    assert captured.get("conversation_id") == "conv-1"
    assert captured.get("ticket_id", "").startswith("COMPLAINT-")


def test_complaint_expert_survives_store_failure():
    """落库失败绝不阻断投诉安抚与转人工。"""
    from backend.customer_service.experts import complaint as ce

    state = {
        "user_id": "u1",
        "session_id": "conv-2",
        "conversation_id": "conv-2",
        "cs_context": {},
    }

    class _BoomStore:
        def create_sync(self, **fields):
            raise RuntimeError("db down")

    with patch(
        "backend.customer_service.ticket_store.get_ticket_store",
        return_value=_BoomStore(),
    ), patch(
        "backend.customer_service.handoff_store.get_handoff_store",
    ), patch(
        "backend.observability.metrics.record_cs_handoff",
    ), patch(
        "backend.customer_service.realtime.get_agent_hub",
    ):
        result = ce.execute_complaint("投诉！虚假宣传！", state)

    assert result["status"] == "success"
    assert result["response_draft"]  # 安抚话术照常


# ── query_expert 工单格式化 ──────────────────────────────


def test_format_ticket_list_empty():
    from backend.customer_service.experts.query import _format_ticket_list

    text = _format_ticket_list([])
    assert "没有进行中的工单" in text


def test_format_ticket_list_items():
    from backend.customer_service.experts.query import _format_ticket_list

    text = _format_ticket_list([
        {
            "ticket_id": "COMPLAINT-ABC",
            "type": "complaint",
            "status": "processing",
            "title": "投诉工单（high）：服务态度差",
        },
    ])
    assert "COMPLAINT-ABC" in text
    assert "处理中" in text
    assert "投诉" in text


def test_ticket_intent_mapped_to_ticket_service():
    from backend.customer_service.experts.query import _INTENT_SERVICE_MAP

    assert _INTENT_SERVICE_MAP.get("t_ticket_status") == "ticket"
