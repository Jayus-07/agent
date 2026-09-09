"""test_handoff.py — 人工转接状态机测试"""
import pytest

from backend.customer_service.handoff import (
    HandoffState,
    HandoffTransitionError,
    TriggerType,
    detect_handoff_trigger,
    evaluate_auto_triggers,
    is_ai_active,
    is_terminal,
    should_intercept,
    transition,
)


class TestValidTransitions:

    def test_ai_active_to_handoff_requested(self):
        result = transition(HandoffState.AI_ACTIVE, HandoffState.HANDOFF_REQUESTED)
        assert result.state == HandoffState.HANDOFF_REQUESTED
        assert result.changed is True

    def test_ai_active_to_closed(self):
        result = transition(HandoffState.AI_ACTIVE, HandoffState.CLOSED)
        assert result.state == HandoffState.CLOSED
        assert result.changed is True

    def test_handoff_requested_to_waiting_human(self):
        result = transition(HandoffState.HANDOFF_REQUESTED, HandoffState.WAITING_HUMAN)
        assert result.state == HandoffState.WAITING_HUMAN
        assert result.changed is True

    def test_handoff_requested_to_ai_active_rollback(self):
        result = transition(HandoffState.HANDOFF_REQUESTED, HandoffState.AI_ACTIVE)
        assert result.state == HandoffState.AI_ACTIVE
        assert result.changed is True

    def test_waiting_human_to_human_active(self):
        result = transition(HandoffState.WAITING_HUMAN, HandoffState.HUMAN_ACTIVE)
        assert result.state == HandoffState.HUMAN_ACTIVE
        assert result.changed is True

    def test_human_active_to_closed(self):
        result = transition(HandoffState.HUMAN_ACTIVE, HandoffState.CLOSED)
        assert result.state == HandoffState.CLOSED
        assert result.changed is True


class TestInvalidTransitions:

    def test_ai_active_to_human_active_invalid(self):
        with pytest.raises(HandoffTransitionError):
            transition(HandoffState.AI_ACTIVE, HandoffState.HUMAN_ACTIVE)

    def test_closed_to_anything_invalid(self):
        for target in HandoffState:
            if target != HandoffState.CLOSED:
                with pytest.raises(HandoffTransitionError):
                    transition(HandoffState.CLOSED, target)

    def test_human_active_to_ai_active_invalid(self):
        with pytest.raises(HandoffTransitionError):
            transition(HandoffState.HUMAN_ACTIVE, HandoffState.AI_ACTIVE)


class TestSameStateTransition:

    def test_same_state_no_change(self):
        result = transition(HandoffState.AI_ACTIVE, HandoffState.AI_ACTIVE)
        assert result.state == HandoffState.AI_ACTIVE
        assert result.changed is False


class TestTerminalState:

    def test_closed_is_terminal(self):
        assert is_terminal(HandoffState.CLOSED) is True

    def test_ai_active_not_terminal(self):
        assert is_terminal(HandoffState.AI_ACTIVE) is False

    def test_human_active_not_terminal(self):
        assert is_terminal(HandoffState.HUMAN_ACTIVE) is False


class TestIsAiActive:

    def test_ai_active(self):
        assert is_ai_active(HandoffState.AI_ACTIVE) is True

    def test_other_states(self):
        for state in HandoffState:
            if state != HandoffState.AI_ACTIVE:
                assert is_ai_active(state) is False


class TestShouldIntercept:

    def test_handoff_requested_intercepts(self):
        assert should_intercept(HandoffState.HANDOFF_REQUESTED) is True

    def test_waiting_human_intercepts(self):
        assert should_intercept(HandoffState.WAITING_HUMAN) is True

    def test_human_active_intercepts(self):
        assert should_intercept(HandoffState.HUMAN_ACTIVE) is True

    def test_ai_active_no_intercept(self):
        assert should_intercept(HandoffState.AI_ACTIVE) is False

    def test_closed_no_intercept(self):
        assert should_intercept(HandoffState.CLOSED) is False


class TestDetectHandoffTrigger:

    def test_explicit_request_transfer(self):
        trigger = detect_handoff_trigger("帮我转人工")
        assert trigger is not None
        assert trigger.trigger_type == TriggerType.EXPLICIT_REQUEST

    def test_explicit_request_human(self):
        trigger = detect_handoff_trigger("我要找客服")
        assert trigger is not None
        assert trigger.trigger_type == TriggerType.EXPLICIT_REQUEST

    def test_keyword_robot(self):
        trigger = detect_handoff_trigger("不要机器人服务")
        assert trigger is not None
        assert trigger.trigger_type == TriggerType.EXPLICIT_REQUEST

    def test_normal_text_no_trigger(self):
        trigger = detect_handoff_trigger("我想查一下订单")
        assert trigger is None

    def test_empty_text_no_trigger(self):
        trigger = detect_handoff_trigger("")
        assert trigger is None


class TestEvaluateAutoTriggers:

    def test_low_confidence_trigger(self):
        ctx = {"consecutive_low_confidence": 3, "last_confidence": 0.2}
        trigger = evaluate_auto_triggers(ctx)
        assert trigger is not None
        assert trigger.trigger_type == TriggerType.LOW_CONFIDENCE

    def test_consecutive_failures_trigger(self):
        ctx = {"consecutive_failures": 3}
        trigger = evaluate_auto_triggers(ctx)
        assert trigger is not None
        assert trigger.trigger_type == TriggerType.CONSECUTIVE_FAILURES

    def test_no_trigger_normal_context(self):
        ctx = {"consecutive_low_confidence": 0, "consecutive_failures": 0}
        trigger = evaluate_auto_triggers(ctx)
        assert trigger is None

    def test_empty_context_no_trigger(self):
        trigger = evaluate_auto_triggers({})
        assert trigger is None
