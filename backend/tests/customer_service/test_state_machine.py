"""test_state_machine.py — Conversation dual-dimension state machine tests

Tests valid/invalid transitions and invariants without DB.
"""
from unittest.mock import MagicMock

import pytest

from backend.customer_service.state_machine import (
    VALID_MODE_TRANSITIONS,
    VALID_STATUS_TRANSITIONS,
    ConvStatus,
    HandlingMode,
    InvalidTransitionError,
    InvariantViolationError,
    TransitionResult,
    apply,
    transition,
)


def _make_conv(
    status: str = "open",
    mode: str = "ai",
    assigned_agent_id: str | None = None,
) -> MagicMock:
    conv = MagicMock()
    conv.conversation_status = status
    conv.handling_mode = mode
    conv.assigned_agent_id = assigned_agent_id
    return conv


class TestValidStatusTransitions:
    @pytest.mark.parametrize("src,dst", [
        ("open", "pending"),
        ("open", "resolved"),
        ("open", "snoozed"),
        ("pending", "open"),
        ("pending", "resolved"),
        ("resolved", "open"),
        ("snoozed", "open"),
    ])
    def test_allowed_status_transitions(self, src, dst):
        conv = _make_conv(status=src)
        result = transition(conv, new_status=ConvStatus(dst))
        assert result.conversation_status == ConvStatus(dst)
        assert result.changed is True

    @pytest.mark.parametrize("status", ["open", "pending", "resolved", "snoozed"])
    def test_same_status_no_change(self, status):
        conv = _make_conv(status=status)
        result = transition(conv, new_status=ConvStatus(status))
        assert result.changed is False


class TestInvalidStatusTransitions:
    @pytest.mark.parametrize("src,dst", [
        ("resolved", "pending"),
        ("resolved", "snoozed"),
        ("snoozed", "pending"),
        ("snoozed", "resolved"),
        ("pending", "snoozed"),
    ])
    def test_disallowed_status_transitions(self, src, dst):
        conv = _make_conv(status=src)
        with pytest.raises(InvalidTransitionError, match="conversation_status"):
            transition(conv, new_status=ConvStatus(dst))


class TestValidModeTransitions:
    @pytest.mark.parametrize("src,dst", [
        ("ai", "human"),
        ("ai", "waiting_human"),
        ("human", "ai"),
        ("waiting_human", "human"),
        ("waiting_human", "ai"),
    ])
    def test_allowed_mode_transitions(self, src, dst):
        conv = _make_conv(mode=src, assigned_agent_id="agent-1" if dst == "human" else None)
        result = transition(conv, new_mode=HandlingMode(dst))
        assert result.handling_mode == HandlingMode(dst)
        assert result.changed is True

    @pytest.mark.parametrize("mode", ["ai", "human", "waiting_human"])
    def test_same_mode_no_change(self, mode):
        conv = _make_conv(mode=mode, assigned_agent_id="agent-1" if mode == "human" else None)
        result = transition(conv, new_mode=HandlingMode(mode))
        assert result.changed is False


class TestInvalidModeTransitions:
    def test_human_cannot_go_to_waiting_human(self):
        conv = _make_conv(mode="human", assigned_agent_id="agent-1")
        with pytest.raises(InvalidTransitionError, match="handling_mode"):
            transition(conv, new_mode=HandlingMode.WAITING_HUMAN)


class TestInvariants:
    def test_resolved_must_be_ai_mode(self):
        conv = _make_conv(status="open", mode="human", assigned_agent_id="agent-1")
        with pytest.raises(InvariantViolationError, match="handling_mode=ai"):
            transition(conv, new_status=ConvStatus.RESOLVED)

    def test_resolved_with_ai_mode_succeeds(self):
        conv = _make_conv(status="open", mode="ai")
        result = transition(conv, new_status=ConvStatus.RESOLVED)
        assert result.conversation_status == ConvStatus.RESOLVED

    def test_human_mode_requires_assigned_agent(self):
        conv = _make_conv(status="open", mode="ai", assigned_agent_id=None)
        with pytest.raises(InvariantViolationError, match="assigned agent"):
            transition(conv, new_mode=HandlingMode.HUMAN)

    def test_human_mode_with_agent_succeeds(self):
        conv = _make_conv(status="open", mode="ai", assigned_agent_id="agent-1")
        result = transition(conv, new_mode=HandlingMode.HUMAN)
        assert result.handling_mode == HandlingMode.HUMAN


class TestDualDimensionTransitions:
    def test_status_and_mode_change_together(self):
        conv = _make_conv(status="open", mode="ai")
        result = transition(
            conv,
            new_status=ConvStatus.RESOLVED,
            new_mode=HandlingMode.AI,
        )
        assert result.conversation_status == ConvStatus.RESOLVED
        assert result.handling_mode == HandlingMode.AI
        assert result.changed is True

    def test_no_change_returns_changed_false(self):
        conv = _make_conv(status="open", mode="ai")
        result = transition(conv)
        assert result.changed is False


class TestApply:
    def test_apply_mutates_conversation(self):
        conv = _make_conv(status="open", mode="ai")
        result = TransitionResult(
            conversation_status=ConvStatus.PENDING,
            handling_mode=HandlingMode.AI,
            changed=True,
        )
        apply(conv, result)
        assert conv.conversation_status == "pending"
        assert conv.handling_mode == "ai"


class TestTransitionCompleteness:
    def test_all_statuses_have_transition_rules(self):
        for status in ConvStatus:
            assert status in VALID_STATUS_TRANSITIONS

    def test_all_modes_have_transition_rules(self):
        for mode in HandlingMode:
            assert mode in VALID_MODE_TRANSITIONS
