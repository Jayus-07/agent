"""STOP CS-A P0-6 回归：handoff lifecycle 唯一入口 + 状态机恢复边 + 幽灵态。"""
from __future__ import annotations

import pytest

from backend.customer_service.errors import BusinessRuleError
from backend.customer_service.handoff import HandoffState, transition
from backend.customer_service.handoff.lifecycle import (
    HANDOFF_TO_HANDLING,
    handling_mode_for,
)


class TestStateMachineRecoveryEdge:
    """P0-5：human_active → waiting_human 是合法恢复边（坐席离线自愈/主管重派）。"""

    def test_human_active_to_waiting_human_valid(self):
        result = transition(HandoffState.HUMAN_ACTIVE, HandoffState.WAITING_HUMAN)
        assert result.changed is True
        assert result.state is HandoffState.WAITING_HUMAN

    def test_human_active_to_closed_still_valid(self):
        assert transition(
            HandoffState.HUMAN_ACTIVE, HandoffState.CLOSED,
        ).state is HandoffState.CLOSED

    def test_closed_is_terminal(self):
        with pytest.raises(BusinessRuleError):
            transition(HandoffState.CLOSED, HandoffState.WAITING_HUMAN)


class TestHandlingModeProjection:

    def test_mapping_covers_all_canonical_states(self):
        from backend.customer_service.models.handoff import HANDOFF_STATES

        assert set(HANDOFF_TO_HANDLING) == set(HANDOFF_STATES)
        for state in HANDOFF_STATES:
            assert handling_mode_for(state) in ("ai", "human", "waiting_human")

    def test_no_initiated_in_canonical_enum(self):
        """幽灵状态 initiated 已从 canonical 枚举移除（migration 078 收敛）。"""
        assert "initiated" not in HandoffState.__members__
        assert "initiated" not in __import__(
            "backend.customer_service.models.handoff", fromlist=["HANDOFF_STATES"],
        ).HANDOFF_STATES


class TestTransitionHandoff:

    @pytest.mark.asyncio
    async def test_illegal_transition_raises_before_any_write(self):
        """非法迁移在触碰任何字段前抛错（session 可为任意对象）。"""

        class _Dummy:
            handoff_state = "closed"
            conversation_id = "c1"
            handoff_id = "h1"
            assignment_version = 0
            updated_at = None

        from datetime import datetime, timezone

        with pytest.raises(BusinessRuleError):
            await __import__(
                "backend.customer_service.handoff.lifecycle",
                fromlist=["transition_handoff"],
            ).transition_handoff(
                session=object(),
                tenant_id="t",
                handoff=_Dummy(),
                conversation=None,
                target_state="waiting_human",
                now=datetime.now(timezone.utc),
            )

    @pytest.mark.asyncio
    async def test_recovery_transition_updates_projection(self, monkeypatch):
        """human_active → waiting_human：状态/坐席/版本/投影/outbox 全链维护。"""
        from datetime import datetime, timezone

        from backend.customer_service.handoff import lifecycle
        from backend.customer_service.models.assignment import CSAssignment
        from backend.customer_service.models.conversation import CSConversation
        from backend.customer_service.models.handoff import CSHandoff

        now = datetime.now(timezone.utc)
        handoff = CSHandoff(
            handoff_id="h1", conversation_id="c1", user_id="u1",
            tenant_id="t1", handoff_state="human_active",
            assigned_agent_id="agent-1", assignment_version=3,
        )
        conversation = CSConversation(
            conversation_id="c1", user_id="u1", tenant_id="t1",
            handling_mode="human", assigned_agent_id="agent-1",
        )

        appended = []

        class _Session:
            async def flush(self):
                return None

        async def _lock(session, *, tenant_id, handoff_id):
            a = CSAssignment(
                tenant_id=tenant_id, handoff_id=handoff_id,
                conversation_id="c1", agent_id="agent-1", state="accepted",
            )
            return [a]

        monkeypatch.setattr(
            lifecycle.repository,
            "lock_active_assignments_for_handoff",
            _lock,
        )
        monkeypatch.setattr(
            lifecycle.outbox, "append_event",
            lambda session, **kw: appended.append(kw),
        )

        await lifecycle.transition_handoff(
            _Session(),
            tenant_id="t1",
            handoff=handoff,
            conversation=conversation,
            target_state="waiting_human",
            clear_assigned_agent=True,
            bump_assignment_version=True,
            release_assignments="released",
            clear_offer_fields=True,
            event_type="conversation.human_active_recovered",
            event_payload={"reason": "agent_offline_recovery"},
            actor_user_id="cs_reaper",
            now=now,
        )

        assert handoff.handoff_state == "waiting_human"
        assert handoff.assigned_agent_id is None
        assert handoff.assignment_version == 4
        assert conversation.handling_mode == "waiting_human"
        assert conversation.assigned_agent_id is None
        assert len(appended) == 1
        assert appended[0]["type"] == "conversation.human_active_recovered"
        assert appended[0]["payload"]["previous_state"] == "human_active"


class TestEnterWaitingSyncBridge:

    def test_non_strict_db_failure_returns_fallback(self, monkeypatch):
        """非严格模式（单测）：DB 失败返回内存快照，不阻断专家流程。"""
        from backend.customer_service.handoff import lifecycle

        def _boom(op):
            raise RuntimeError("db down")

        monkeypatch.setattr(
            "backend.customer_service._db_loop.run_sync", _boom,
        )
        monkeypatch.setattr(
            "backend.customer_service.confirmation_store._strict_writes",
            lambda: False,
        )
        result = lifecycle.enter_waiting_handoff_sync(
            tenant_id="t1", conversation_id="c1", user_id="u1",
            trigger_type="explicit_request", trigger_reason="r", ticket_id="T1",
        )
        assert result["handoff_state"] == HandoffState.WAITING_HUMAN.value

    def test_strict_db_failure_raises_store_write_error(self, monkeypatch):
        """严格模式（生产默认）：工单不持久化就必须失败。"""
        from backend.customer_service.confirmation_store import StoreWriteError
        from backend.customer_service.handoff import lifecycle

        def _boom(op):
            raise RuntimeError("db down")

        monkeypatch.setattr(
            "backend.customer_service._db_loop.run_sync", _boom,
        )
        monkeypatch.setattr(
            "backend.customer_service.confirmation_store._strict_writes",
            lambda: True,
        )
        with pytest.raises(StoreWriteError):
            lifecycle.enter_waiting_handoff_sync(
                tenant_id="t1", conversation_id="c1", user_id="u1",
                trigger_type="explicit_request", trigger_reason="r",
                ticket_id="T1",
            )


@pytest.mark.asyncio
async def test_enter_waiting_handoff_locks_owned_conversation_before_handoff(monkeypatch):
    from types import SimpleNamespace

    from sqlalchemy.dialects import postgresql

    from backend.customer_service.handoff import lifecycle

    conversation = SimpleNamespace(
        conversation_id="c1", user_id="u1", tenant_id="t1",
        handling_mode="ai", updated_at=None, last_activity_at=None,
    )
    captured = []

    class _Result:
        def __init__(self, row):
            self._row = row

        def scalar_one_or_none(self):
            return self._row

    class _Session:
        def add(self, row):
            return None

        async def execute(self, statement):
            captured.append(statement)
            return _Result(conversation if len(captured) == 1 else None)

        async def flush(self):
            return None

    class _ConversationManager:
        def __init__(self, _session):
            pass

        async def get_or_create(self, _conversation_id, _user_id, **_kwargs):
            return conversation, False

    monkeypatch.setattr(
        "backend.customer_service.managers.conversation_manager.ConversationManager",
        _ConversationManager,
    )

    await lifecycle.enter_waiting_handoff(
        _Session(), tenant_id="t1", conversation_id="c1", user_id="u1",
        trigger_type="explicit_request", trigger_reason="reason", ticket_id="T1",
    )

    first_sql = str(captured[0].compile(dialect=postgresql.dialect()))
    second_sql = str(captured[1].compile(dialect=postgresql.dialect()))
    assert "customer_service.conversations" in first_sql
    assert "FOR UPDATE" in first_sql
    assert "customer_service.handoffs" in second_sql


@pytest.mark.asyncio
async def test_enter_waiting_handoff_rejects_conversation_owned_by_other_user(monkeypatch):
    from types import SimpleNamespace

    from backend.customer_service.handoff import lifecycle

    conversation = SimpleNamespace(
        conversation_id="c1", user_id="someone-else", tenant_id="t1",
    )

    class _ConversationManager:
        def __init__(self, _session):
            pass

        async def get_or_create(self, _conversation_id, _user_id, **_kwargs):
            return conversation, False

    monkeypatch.setattr(
        "backend.customer_service.managers.conversation_manager.ConversationManager",
        _ConversationManager,
    )

    with pytest.raises(BusinessRuleError):
        await lifecycle.enter_waiting_handoff(
            object(), tenant_id="t1", conversation_id="c1", user_id="u1",
            trigger_type="explicit_request", trigger_reason="reason", ticket_id="T1",
        )
def test_authoritative_handoff_read_can_fail_closed(monkeypatch):
    import pytest
    import backend.customer_service._db_loop as db_loop
    from backend.customer_service.handoff.lifecycle import load_active_handoff_sync

    def database_down(_operation):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(db_loop, "run_sync", database_down)

    with pytest.raises(RuntimeError, match="database unavailable"):
        load_active_handoff_sync("tenant-1", "conversation-1", raise_on_error=True)
