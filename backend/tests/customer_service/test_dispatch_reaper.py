"""P7 reaper 测试：过期 offer 回收、超预算终态关闭、超总等待期关闭。

reaper 是纯时间驱动的状态机边（方案 §五），与 dispatcher / offers 共用
``conversations → handoffs`` 加锁顺序。测试覆盖：

- 30 秒 offer 超时、attempt 预算未用尽 → 回队列（``released``）
- attempt ≥ 5 或超 600 秒总等待 → 终态关闭 + 会话恢复 AI（``closed``）
- ``waiting_human`` 超总等待期 → 关闭（P6 遗留的 Q5 终态）
- 锁竞争（``SKIP LOCKED``）→ 计入 ``contended``，不改任何行
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.customer_service.dispatch import reaper, repository
from backend.customer_service.models.assignment import CSAssignment
from backend.customer_service.models.conversation import CSConversation
from backend.customer_service.models.event import CSEvent
from backend.customer_service.models.handoff import CSHandoff

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
TENANT = "tenant-a"

EXPIRED_AT = NOW - timedelta(seconds=5)


def _handoff(**overrides) -> CSHandoff:
    values = dict(
        handoff_id="hd-1",
        conversation_id="conv-1",
        user_id="user-1",
        tenant_id=TENANT,
        handoff_state="agent_offered",
        trigger_type="explicit_request",
        priority=50,
        assignment_version=2,
        attempt_count=1,
        assigned_agent_id="agent-1",
        offered_at=EXPIRED_AT - timedelta(seconds=30),
        offer_expires_at=EXPIRED_AT,
        created_at=NOW - timedelta(seconds=120),
        updated_at=EXPIRED_AT,
        total_deadline_at=NOW + timedelta(seconds=480),
    )
    values.update(overrides)
    return CSHandoff(**values)


def _conversation(**overrides) -> CSConversation:
    values = dict(
        conversation_id="conv-1",
        user_id="user-1",
        tenant_id=TENANT,
        conversation_status="open",
        handling_mode="waiting_human",
        assigned_agent_id="agent-1",
    )
    values.update(overrides)
    return CSConversation(**values)


def _assignment(**overrides) -> CSAssignment:
    values = dict(
        tenant_id=TENANT,
        handoff_id="hd-1",
        conversation_id="conv-1",
        agent_id="agent-1",
        state="offered",
        attempt_no=1,
        offer_version=2,
        offered_at=EXPIRED_AT - timedelta(seconds=30),
        offer_expires_at=EXPIRED_AT,
        assigned_by="cs_dispatcher",
        assigned_at=EXPIRED_AT - timedelta(seconds=30),
    )
    values.update(overrides)
    return CSAssignment(**values)


class Scenario:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.handoff = _handoff()
        self.conversation = _conversation()
        self.assignments = [_assignment()]
        self.expired_candidates: list[tuple[str, str, str]] = [
            (TENANT, "hd-1", "conv-1")
        ]
        self.overdue_candidates: list[tuple[str, str, str]] = []
        self.session = FakeSession()
        self.calls: list[str] = []
        self._install(monkeypatch)

    def _install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def list_expired(*_a, **_k):
            self.calls.append("list_expired")
            return list(self.expired_candidates)

        async def list_overdue(*_a, **_k):
            self.calls.append("list_overdue")
            return list(self.overdue_candidates)

        async def lock_conversation(*_a, **_k):
            self.calls.append("lock_conversation")
            return self.conversation

        async def lock_expired_handoff(*_a, **_k):
            self.calls.append("lock_expired_handoff")
            return self.handoff

        async def lock_overdue_handoff(*_a, **_k):
            self.calls.append("lock_overdue_handoff")
            return self.handoff

        async def lock_assignments(*_a, **_k):
            self.calls.append("lock_assignments")
            return list(self.assignments)

        monkeypatch.setattr(repository, "list_expired_offer_candidates", list_expired)
        monkeypatch.setattr(repository, "list_overdue_waiting_candidates", list_overdue)
        monkeypatch.setattr(repository, "lock_conversation", lock_conversation)
        monkeypatch.setattr(
            repository, "lock_expired_offer_handoff", lock_expired_handoff
        )
        monkeypatch.setattr(
            repository, "lock_overdue_waiting_handoff", lock_overdue_handoff
        )
        monkeypatch.setattr(
            repository, "lock_active_assignments_for_handoff", lock_assignments
        )

    def events(self) -> list[CSEvent]:
        # reaper 不持有 session 引用；事件通过 repository 捕获不方便，
        # 这里改为检查 handoff/conversation/assignment 的状态 + monkeypatch
        # outbox.append_event 的调用记录由用例按需安装。
        return []


@pytest.fixture
def scenario(monkeypatch: pytest.MonkeyPatch) -> Scenario:
    return Scenario(monkeypatch)


class _Txn:
    def __init__(self, session: "FakeSession") -> None:
        self._session = session

    async def __aenter__(self) -> "FakeSession":
        return self._session

    async def __aexit__(self, exc_type, _exc, _tb) -> bool:
        return False


class FakeSession:
    """reaper 不开事务（事务边界由 worker 持有），但 outbox 需要 add 面。"""

    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None


async def test_expired_offer_is_released_back_to_queue(scenario: Scenario) -> None:
    result = await reaper.reap_expired_offers(scenario.session, now=NOW, limit=50)

    assert result == reaper.ReapResult(scanned=1, released=1)
    assert scenario.handoff.handoff_state == "waiting_human"
    assert scenario.handoff.assigned_agent_id is None
    assert scenario.handoff.offered_at is None
    assert scenario.handoff.offer_expires_at is None
    assert scenario.handoff.updated_at == NOW

    assert scenario.assignments[0].state == "expired"
    assert scenario.assignments[0].unassigned_at == NOW

    assert scenario.conversation.assigned_agent_id is None
    assert scenario.conversation.handling_mode == "waiting_human"
    # 锁序：会话先于工单。
    assert scenario.calls.index("lock_conversation") < scenario.calls.index(
        "lock_expired_handoff"
    )


async def test_expired_offer_with_exhausted_attempts_is_closed(
    scenario: Scenario,
) -> None:
    scenario.handoff.attempt_count = 5

    result = await reaper.reap_expired_offers(scenario.session, now=NOW, limit=50)

    assert result.closed == 1
    assert result.released == 0
    assert scenario.handoff.handoff_state == "closed"
    assert "max_attempts" in (scenario.handoff.closed_reason or "")
    assert scenario.handoff.assigned_agent_id is None
    # 恢复 AI：会话回 AI 处理、坐席归属清空（「通知用户并恢复 AI」）。
    assert scenario.conversation.handling_mode == "ai"
    assert scenario.conversation.assigned_agent_id is None
    assert scenario.assignments[0].state == "expired"


async def test_expired_offer_past_total_deadline_is_closed(
    scenario: Scenario,
) -> None:
    scenario.handoff.attempt_count = 1
    scenario.handoff.total_deadline_at = NOW - timedelta(seconds=1)

    result = await reaper.reap_expired_offers(scenario.session, now=NOW, limit=50)

    assert result.closed == 1
    assert "total_deadline" in (scenario.handoff.closed_reason or "")


async def test_contended_conversation_is_counted_and_untouched(
    scenario: Scenario, monkeypatch
) -> None:
    async def locked(*_a, **_k):
        return None

    monkeypatch.setattr(repository, "lock_conversation", locked)

    result = await reaper.reap_expired_offers(scenario.session, now=NOW, limit=50)

    assert result == reaper.ReapResult(scanned=1, contended=1)
    assert scenario.handoff.handoff_state == "agent_offered"
    assert "lock_expired_handoff" not in scenario.calls


async def test_overdue_waiting_handoff_is_closed(scenario: Scenario) -> None:
    scenario.expired_candidates = []
    scenario.overdue_candidates = [(TENANT, "hd-2", "conv-1")]
    scenario.handoff = _handoff(
        handoff_id="hd-2",
        handoff_state="waiting_human",
        assigned_agent_id=None,
        total_deadline_at=NOW - timedelta(seconds=1),
    )
    scenario.assignments = []

    result = await reaper.reap_overdue_waiting(scenario.session, now=NOW, limit=50)

    assert result.closed == 1
    assert scenario.handoff.handoff_state == "closed"
    assert "total_deadline" in (scenario.handoff.closed_reason or "")
    assert scenario.conversation.handling_mode == "ai"


async def test_reap_once_runs_expired_before_overdue(scenario: Scenario) -> None:
    scenario.overdue_candidates = [(TENANT, "hd-2", "conv-1")]

    result = await reaper.reap_once(scenario.session, now=NOW)

    assert result.scanned == 2
    assert result.released == 1
    assert result.closed == 1
    # 顺序不可颠倒：agent_offered 的超期行必须先被 expired 阶段看到。
    assert scenario.calls.index("list_expired") < scenario.calls.index("list_overdue")


async def test_reaper_close_writes_outbox_event(
    scenario: Scenario, monkeypatch
) -> None:
    """终态关闭必须落 outbox 事件（P8 relay 负责投递给用户/坐席）。"""
    appended: list[dict] = []

    def fake_append(session, **kwargs):
        appended.append(kwargs)
        return None

    monkeypatch.setattr(reaper.outbox, "append_event", fake_append)
    scenario.handoff.attempt_count = 5

    await reaper.reap_expired_offers(scenario.session, now=NOW, limit=50)

    assert len(appended) == 1
    call = appended[0]
    assert call["type"] == "conversation.handoff_closed"
    assert call["payload"]["resume_ai"] is True
    assert call["payload"]["notify_user"] is True
    assert call["payload"]["reason"] == "max_attempts"
    assert call["actor_user_id"] == "cs_reaper"


async def test_reaper_release_writes_expired_event(
    scenario: Scenario, monkeypatch
) -> None:
    appended: list[dict] = []

    def fake_append(session, **kwargs):
        appended.append(kwargs)
        return None

    monkeypatch.setattr(reaper.outbox, "append_event", fake_append)

    await reaper.reap_expired_offers(scenario.session, now=NOW, limit=50)

    assert len(appended) == 1
    call = appended[0]
    assert call["type"] == "conversation.offer_expired"
    assert call["payload"]["previous_agent_id"] == "agent-1"
    assert call["payload"]["attempt_count"] == 1
