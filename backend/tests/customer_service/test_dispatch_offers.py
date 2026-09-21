"""P7 坐席 offer 生命周期测试（accept / decline / reassign / 列表）。

与 ``test_dispatch_service.py`` 同一套 fake-session 方法论：脚本化
repository 步骤，断言状态迁移、assignment 终结、outbox 事件与事务边界。
方案 §六 P7 完成标准的 API 层语义（403/409/404）由
``test_cs_agent_offers_api.py`` 以路由级用例锁定。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.customer_service.dispatch import offers, repository
from backend.config.customer_service import CS_HANDOFF_TIMEOUT_SECONDS
from backend.customer_service.models.assignment import CSAssignment
from backend.customer_service.models.conversation import CSConversation
from backend.customer_service.models.event import CSEvent
from backend.customer_service.models.handoff import CSHandoff

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
TENANT = "tenant-a"

# ── fake 基建（与 test_dispatch_service.py 同构）─────────────


class _Txn:
    def __init__(self, session: "FakeSession") -> None:
        self._session = session

    async def __aenter__(self) -> "FakeSession":
        self._session.records.append("begin")
        return self._session

    async def __aexit__(self, exc_type, _exc, _tb) -> bool:
        self._session.records.append("rollback" if exc_type is not None else "commit")
        return False


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.records: list[str] = []

    def begin(self) -> _Txn:
        return _Txn(self)

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.records.append("flush")


def _handoff(**overrides) -> CSHandoff:
    values = dict(
        handoff_id="hd-1",
        conversation_id="conv-1",
        user_id="user-1",
        tenant_id=TENANT,
        handoff_state="agent_offered",
        trigger_type="explicit_request",
        priority=50,
        assignment_version=3,
        attempt_count=1,
        assigned_agent_id="agent-1",
        offered_at=NOW - timedelta(seconds=5),
        offer_expires_at=NOW + timedelta(seconds=25),
        created_at=NOW - timedelta(seconds=60),
        updated_at=NOW - timedelta(seconds=5),
        total_deadline_at=NOW + timedelta(seconds=540),
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


def _assignment(agent_id: str = "agent-1", **overrides) -> CSAssignment:
    values = dict(
        tenant_id=TENANT,
        handoff_id="hd-1",
        conversation_id="conv-1",
        agent_id=agent_id,
        state="offered",
        attempt_no=1,
        offer_version=3,
        offered_at=NOW - timedelta(seconds=5),
        offer_expires_at=NOW + timedelta(seconds=25),
        assigned_by="cs_dispatcher",
        assigned_at=NOW - timedelta(seconds=5),
    )
    values.update(overrides)
    return CSAssignment(**values)


class Scenario:
    """offers 服务的可覆盖步骤；未覆盖走默认（被分配、状态新鲜）。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.session = FakeSession()
        self.handoff = _handoff()
        self.conversation = _conversation()
        self.assignments = [_assignment("agent-1")]
        self.my_rows: list[CSHandoff] = [self.handoff]
        self.calls: list[str] = []
        self._install(monkeypatch)

    def _install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def find_conversation_id(*_a, **_k):
            self.calls.append("find_conversation_id")
            return self.handoff.conversation_id

        async def lock_conversation(*_a, **_k):
            self.calls.append("lock_conversation")
            return self.conversation

        async def lock_handoff(*_a, **_k):
            self.calls.append("lock_handoff")
            return self.handoff

        async def lock_assignments(*_a, **_k):
            self.calls.append("lock_assignments")
            return list(self.assignments)

        async def list_my(*_a, **_k):
            self.calls.append("list_my")
            return list(self.my_rows)

        async def lock_agent(*_a, **_k):
            self.calls.append("lock_agent")
            return self.target_agent

        self.target_agent = None
        monkeypatch.setattr(repository, "find_handoff_conversation_id", find_conversation_id)
        monkeypatch.setattr(repository, "lock_conversation", lock_conversation)
        monkeypatch.setattr(repository, "lock_handoff_by_id", lock_handoff)
        monkeypatch.setattr(
            repository, "lock_active_assignments_for_handoff", lock_assignments
        )
        monkeypatch.setattr(repository, "list_my_handoffs", list_my)
        monkeypatch.setattr(repository, "lock_least_loaded_agent", lock_agent)

    def outbox_events(self) -> list[CSEvent]:
        return [o for o in self.session.added if isinstance(o, CSEvent)]


@pytest.fixture
def scenario(monkeypatch: pytest.MonkeyPatch) -> Scenario:
    return Scenario(monkeypatch)


# ── accept ──────────────────────────────────────────────────


async def test_accept_binds_agent_and_switches_to_human_active(
    scenario: Scenario,
) -> None:
    result = await offers.accept_offer(
        scenario.session,
        tenant_id=TENANT,
        agent_id="agent-1",
        handoff_id="hd-1",
        offer_version=3,
        now=NOW,
    )

    assert result.handoff_state == "human_active"
    assert result.agent_id == "agent-1"
    assert result.assignment_version == 3

    assert scenario.handoff.handoff_state == "human_active"
    assert scenario.handoff.offer_expires_at is None
    assert scenario.handoff.offered_at is None
    assert scenario.handoff.updated_at == NOW

    target = scenario.assignments[0]
    assert target.state == "accepted"
    assert target.accepted_at == NOW

    assert scenario.conversation.assigned_agent_id == "agent-1"
    assert scenario.conversation.handling_mode == "human"

    events = scenario.outbox_events()
    assert [e.type for e in events] == ["conversation.claimed"]
    assert events[0].payload["agent_id"] == "agent-1"
    assert events[0].payload["assignment_version"] == 3

    assert scenario.session.records == ["begin", "flush", "commit"]


async def test_accept_by_another_agent_is_forbidden(scenario: Scenario) -> None:
    with pytest.raises(offers.OfferForbidden):
        await offers.accept_offer(
            scenario.session,
            tenant_id=TENANT,
            agent_id="agent-9",
            handoff_id="hd-1",
            offer_version=3,
            now=NOW,
        )
    assert scenario.handoff.handoff_state == "agent_offered"
    assert scenario.session.records[-1] == "rollback"


async def test_accept_with_stale_version_is_rejected(scenario: Scenario) -> None:
    """重派后再点旧 offer 的接单按钮 → 409，绝不以旧版本覆盖新一轮。"""
    with pytest.raises(offers.OfferStale):
        await offers.accept_offer(
            scenario.session,
            tenant_id=TENANT,
            agent_id="agent-1",
            handoff_id="hd-1",
            offer_version=2,
            now=NOW,
        )
    assert scenario.handoff.handoff_state == "agent_offered"


async def test_accept_after_expiry_is_rejected(scenario: Scenario) -> None:
    scenario.handoff.offer_expires_at = NOW - timedelta(seconds=1)

    with pytest.raises(offers.OfferStale):
        await offers.accept_offer(
            scenario.session,
            tenant_id=TENANT,
            agent_id="agent-1",
            handoff_id="hd-1",
            now=NOW,
        )


async def test_accept_replay_after_already_accepted_is_rejected(
    scenario: Scenario,
) -> None:
    scenario.handoff.handoff_state = "human_active"

    with pytest.raises(offers.OfferStale, match="已由本坐席接单"):
        await offers.accept_offer(
            scenario.session,
            tenant_id=TENANT,
            agent_id="agent-1",
            handoff_id="hd-1",
            offer_version=3,
            now=NOW,
        )


async def test_accept_fails_when_assignment_was_already_reaped(
    scenario: Scenario, monkeypatch
) -> None:
    """assignment 已被 reaper/主管解除但状态未刷新：以 assignment 为准拒绝。"""
    scenario.assignments = []

    with pytest.raises(offers.OfferStale, match="被回收"):
        await offers.accept_offer(
            scenario.session,
            tenant_id=TENANT,
            agent_id="agent-1",
            handoff_id="hd-1",
            offer_version=3,
            now=NOW,
        )
    assert scenario.handoff.handoff_state == "agent_offered"


# ── decline ─────────────────────────────────────────────────


async def test_decline_returns_handoff_to_queue_and_starts_cooldown(
    scenario: Scenario,
) -> None:
    result = await offers.decline_offer(
        scenario.session,
        tenant_id=TENANT,
        agent_id="agent-1",
        handoff_id="hd-1",
        offer_version=3,
        reason="away",
        now=NOW,
    )

    assert result.handoff_state == "waiting_human"
    assert result.agent_id is None
    assert scenario.handoff.handoff_state == "waiting_human"
    assert scenario.handoff.assigned_agent_id is None
    # attempt 不回滚：拒单消耗一次自动派单机会（5 次上限不被绕过）。
    assert scenario.handoff.attempt_count == 1

    target = scenario.assignments[0]
    assert target.state == "declined"
    assert target.declined_at == NOW
    assert target.unassigned_at == NOW
    # 拒单原因落库（030 新列），与事件 payload 同源
    assert target.decline_reason == "away"

    assert scenario.conversation.assigned_agent_id is None
    assert scenario.conversation.handling_mode == "waiting_human"

    events = scenario.outbox_events()
    assert [e.type for e in events] == ["conversation.offer_declined"]
    assert events[0].payload["reason"] == "away"


async def test_decline_by_another_agent_is_forbidden(scenario: Scenario) -> None:
    with pytest.raises(offers.OfferForbidden):
        await offers.decline_offer(
            scenario.session,
            tenant_id=TENANT,
            agent_id="agent-9",
            handoff_id="hd-1",
            now=NOW,
        )


# ── reassign ────────────────────────────────────────────────


async def test_reassign_without_target_resets_retry_budget(
    scenario: Scenario,
) -> None:
    result = await offers.reassign_handoff(
        scenario.session,
        tenant_id=TENANT,
        supervisor_agent_id="sup-1",
        handoff_id="hd-1",
        now=NOW,
    )

    assert result.handoff_state == "waiting_human"
    assert result.agent_id is None
    assert scenario.handoff.assignment_version == 4
    assert scenario.handoff.attempt_count == 0
    # 人工介入把自动重试预算重新计满，总等待期顺延一个完整窗口。
    assert scenario.handoff.total_deadline_at == NOW + timedelta(
        seconds=CS_HANDOFF_TIMEOUT_SECONDS
    )

    assert scenario.assignments[0].state == "released"
    assert scenario.assignments[0].unassigned_at == NOW
    assert scenario.conversation.assigned_agent_id is None

    events = scenario.outbox_events()
    assert [e.type for e in events] == ["conversation.reassigned"]
    assert events[0].payload["reassigned_by"] == "sup-1"


async def test_reassign_to_target_issues_directed_offer(scenario: Scenario) -> None:
    scenario.target_agent = _assignment("agent-2")  # 任意行，仅表示可锁到
    result = await offers.reassign_handoff(
        scenario.session,
        tenant_id=TENANT,
        supervisor_agent_id="sup-1",
        handoff_id="hd-1",
        target_agent_id="agent-2",
        now=NOW,
    )

    assert result.handoff_state == "agent_offered"
    assert result.agent_id == "agent-2"
    assert scenario.handoff.assigned_agent_id == "agent-2"
    assert scenario.handoff.assignment_version == 4
    assert scenario.handoff.offer_expires_at == NOW + timedelta(seconds=30)

    new_assignments = [
        o for o in scenario.session.added if isinstance(o, CSAssignment)
    ]
    assert len(new_assignments) == 1
    assert new_assignments[0].agent_id == "agent-2"
    assert new_assignments[0].state == "offered"
    assert new_assignments[0].assigned_by == "sup-1"
    assert new_assignments[0].offer_version == 4

    events = scenario.outbox_events()
    assert [e.type for e in events] == [
        "conversation.reassigned",
        "conversation.offered",
    ]
    assert events[1].target_agent_id == "agent-2"


async def test_reassign_to_offline_target_is_conflict(
    scenario: Scenario, monkeypatch
) -> None:
    async def no_agent(*_a, **_k):
        return None

    monkeypatch.setattr(repository, "lock_least_loaded_agent", no_agent)

    with pytest.raises(offers.ReassignConflict):
        await offers.reassign_handoff(
            scenario.session,
            tenant_id=TENANT,
            supervisor_agent_id="sup-1",
            handoff_id="hd-1",
            target_agent_id="agent-2",
            now=NOW,
        )


async def test_reassign_closed_handoff_is_conflict(scenario: Scenario) -> None:
    scenario.handoff.handoff_state = "closed"

    with pytest.raises(offers.ReassignConflict):
        await offers.reassign_handoff(
            scenario.session,
            tenant_id=TENANT,
            supervisor_agent_id="sup-1",
            handoff_id="hd-1",
            now=NOW,
        )


async def test_reassign_missing_handoff_is_not_found(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def find_none(*_a, **_k):
        return None

    monkeypatch.setattr(repository, "find_handoff_conversation_id", find_none)

    with pytest.raises(offers.OfferNotFound):
        await offers.reassign_handoff(
            scenario.session,
            tenant_id=TENANT,
            supervisor_agent_id="sup-1",
            handoff_id="hd-missing",
            now=NOW,
        )


# ── list ────────────────────────────────────────────────────


async def test_list_my_offers_maps_rows(scenario: Scenario) -> None:
    rows = await offers.list_my_offers(
        scenario.session, tenant_id=TENANT, agent_id="agent-1"
    )

    assert len(rows) == 1
    item = rows[0]
    assert item.handoff_id == "hd-1"
    assert item.conversation_id == "conv-1"
    assert item.user_id == "user-1"
    assert item.handoff_state == "agent_offered"
    assert item.assignment_version == 3
    assert item.attempt_count == 1
    assert item.priority == 50
    assert item.offer_expires_at == NOW + timedelta(seconds=25)
