"""客服坐席认领与归属落库回归测试。"""

import pytest
from fastapi import HTTPException

from backend.app.api.routes import cs_admin


class _HandoffRow:
    conversation_id = "conv-1"
    user_id = "user-1"
    handoff_state = "waiting_human"


class _Result:
    def __init__(self, row=None, rowcount=None):
        self._row = row
        self.rowcount = rowcount

    def first(self):
        return self._row


class _FakeDB:
    def __init__(self):
        self.execute_count = 0
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, query):
        self.execute_count += 1
        if self.execute_count == 1:
            return _Result(row=_HandoffRow())
        if self.execute_count == 2:
            return _Result(rowcount=1)
        raise AssertionError("认领不应绕过 ConversationManager 再直接写状态")

    async def commit(self):
        self.committed = True


class _FakeConversationManager:
    calls = []

    def __init__(self, db):
        self.db = db

    async def get_or_create(self, conversation_id, user_id):
        self.calls.append(("get_or_create", conversation_id, user_id))
        return object(), False

    async def escalate_to_human(self, conversation_id, agent_id, *, assigned_by):
        self.calls.append(
            ("escalate_to_human", conversation_id, agent_id, assigned_by)
        )


@pytest.mark.asyncio
async def test_claim_persists_conversation_assignment(monkeypatch):
    """认领必须通过会话管理器写入 assigned_agent_id 与 assignment。"""
    db = _FakeDB()
    _FakeConversationManager.calls = []

    monkeypatch.setattr(
        "backend.memory.database.AsyncSessionLocal", lambda: db
    )
    monkeypatch.setattr(
        "backend.customer_service.managers.conversation_manager.ConversationManager",
        _FakeConversationManager,
    )
    monkeypatch.setattr(
        cs_admin, "_ensure_cs_agent", _fake_ensure_cs_agent, raising=False
    )
    monkeypatch.setattr(
        "backend.customer_service.handoff_store.get_handoff_store",
        lambda: _FakeStore(),
    )
    monkeypatch.setattr(
        "backend.customer_service.realtime.get_agent_hub",
        lambda: _FakeHub(),
    )

    result = await cs_admin._async_claim("conv-1", "agent-1", None)

    assert result == {
        "conversation_id": "conv-1",
        "handoff_state": "human_active",
        "agent_id": "agent-1",
        "already_claimed": False,
    }
    assert _FakeConversationManager.calls == [
        ("get_or_create", "conv-1", "user-1"),
        ("escalate_to_human", "conv-1", "agent-1", "agent-1"),
    ]
    assert db.committed is True


def test_agent_actions_require_current_assignment():
    """人工消息/结束会话不能由未认领或其他坐席代发。"""
    with pytest.raises(HTTPException) as missing:
        cs_admin._assert_current_agent(None, "agent-1")
    assert missing.value.status_code == 409

    with pytest.raises(HTTPException) as other:
        cs_admin._assert_current_agent("agent-2", "agent-1")
    assert other.value.status_code == 409

    cs_admin._assert_current_agent("agent-1", "agent-1")


async def _fake_ensure_cs_agent(db, agent_id):
    return None


class _FakeStore:
    def invalidate(self, user_id, conversation_id):
        return None


class _FakeHub:
    def publish(self, event_type, **payload):
        return None


class _RaceResult:
    def __init__(self, *, row=None, rowcount=None, scalar=None):
        self._row = row
        self.rowcount = rowcount
        self._scalar = scalar

    def first(self):
        return self._row

    def scalar_one_or_none(self):
        return self._scalar


class _RaceDB:
    """模拟赢家尚未提交 assignment 时，失败方看到的 DB 快照。"""

    def __init__(self):
        self.execute_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, query):
        self.execute_count += 1
        if self.execute_count == 1:
            return _RaceResult(row=_HandoffRow())
        if self.execute_count == 2:
            return _RaceResult(rowcount=0)
        if self.execute_count == 3:
            return _RaceResult(scalar="human_active")
        if self.execute_count == 4:
            return _RaceResult(scalar=None)
        raise AssertionError("竞态失败方不应继续写入会话或 assignment")


@pytest.mark.asyncio
async def test_claim_race_without_visible_assignment_is_not_success(monkeypatch):
    """赢家未提交 assignment 时，失败方必须返回 409 而非伪成功。"""
    db = _RaceDB()

    monkeypatch.setattr(
        "backend.memory.database.AsyncSessionLocal", lambda: db
    )

    with pytest.raises(HTTPException) as exc_info:
        await cs_admin._async_claim("conv-1", "agent-2", None)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail
