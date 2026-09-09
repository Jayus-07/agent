"""test_confirmation.py — 确认状态机测试"""
from datetime import datetime, timedelta, timezone

import pytest

from backend.customer_service.confirmation import (
    ConfirmationIntent,
    ConfirmationState,
    ConfirmationTransitionError,
    compute_expires_at,
    detect_confirmation_intent,
    is_expired,
    is_terminal,
    transition,
)


class TestValidTransitions:
    @pytest.mark.parametrize("src,dst", [
        (ConfirmationState.NOT_REQUIRED, ConfirmationState.EXECUTING),
        (ConfirmationState.PENDING_CONFIRMATION, ConfirmationState.USER_CONFIRMED),
        (ConfirmationState.PENDING_CONFIRMATION, ConfirmationState.USER_CANCELLED),
        (ConfirmationState.PENDING_CONFIRMATION, ConfirmationState.EXPIRED),
        (ConfirmationState.USER_CONFIRMED, ConfirmationState.EXECUTING),
        (ConfirmationState.EXECUTING, ConfirmationState.SUCCESS),
        (ConfirmationState.EXECUTING, ConfirmationState.FAILED),
    ])
    def test_allowed_transitions(self, src, dst):
        result = transition(src, dst)
        assert result.state == dst
        assert result.changed is True

    @pytest.mark.parametrize("state", list(ConfirmationState))
    def test_same_state_no_change(self, state):
        result = transition(state, state)
        assert result.changed is False
        assert result.state == state


class TestInvalidTransitions:
    @pytest.mark.parametrize("src,dst", [
        (ConfirmationState.PENDING_CONFIRMATION, ConfirmationState.EXECUTING),
        (ConfirmationState.NOT_REQUIRED, ConfirmationState.SUCCESS),
        (ConfirmationState.SUCCESS, ConfirmationState.EXECUTING),
        (ConfirmationState.USER_CANCELLED, ConfirmationState.EXECUTING),
        (ConfirmationState.EXPIRED, ConfirmationState.EXECUTING),
        (ConfirmationState.FAILED, ConfirmationState.SUCCESS),
        (ConfirmationState.EXECUTING, ConfirmationState.PENDING_CONFIRMATION),
        (ConfirmationState.NOT_REQUIRED, ConfirmationState.PENDING_CONFIRMATION),
    ])
    def test_illegal_transition_raises(self, src, dst):
        with pytest.raises(ConfirmationTransitionError):
            transition(src, dst)

    def test_pending_to_executing_is_illegal(self):
        with pytest.raises(ConfirmationTransitionError, match="pending.*executing"):
            transition(
                ConfirmationState.PENDING_CONFIRMATION,
                ConfirmationState.EXECUTING,
            )


class TestTerminalStates:
    @pytest.mark.parametrize("state", [
        ConfirmationState.SUCCESS,
        ConfirmationState.FAILED,
        ConfirmationState.USER_CANCELLED,
        ConfirmationState.EXPIRED,
    ])
    def test_terminal_states(self, state):
        assert is_terminal(state) is True

    @pytest.mark.parametrize("state", [
        ConfirmationState.NOT_REQUIRED,
        ConfirmationState.PENDING_CONFIRMATION,
        ConfirmationState.USER_CONFIRMED,
        ConfirmationState.EXECUTING,
    ])
    def test_non_terminal_states(self, state):
        assert is_terminal(state) is False


class TestDetectConfirmationIntent:
    @pytest.mark.parametrize("text", [
        "确认", "确定", "好的", "同意", "可以", "没问题", "是的",
        "ok", "yes", "confirm",
    ])
    def test_confirm_keywords(self, text):
        assert detect_confirmation_intent(text) == ConfirmationIntent.CONFIRM

    @pytest.mark.parametrize("text", [
        "取消", "算了", "不要", "否", "放弃", "cancel", "no",
    ])
    def test_cancel_keywords(self, text):
        assert detect_confirmation_intent(text) == ConfirmationIntent.CANCEL

    def test_empty_text_returns_none(self):
        assert detect_confirmation_intent("") == ConfirmationIntent.NONE

    def test_whitespace_returns_none(self):
        assert detect_confirmation_intent("   ") == ConfirmationIntent.NONE

    def test_unrelated_text_returns_none(self):
        assert detect_confirmation_intent("我想查订单") == ConfirmationIntent.NONE

    def test_cancel_takes_priority_over_confirm(self):
        result = detect_confirmation_intent("确认取消")
        assert result == ConfirmationIntent.CANCEL


class TestExpiry:
    def test_compute_expires_at(self):
        created = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
        expires = compute_expires_at(created, 900)
        assert expires == created + timedelta(seconds=900)

    def test_is_expired_true(self):
        pending = {
            "expires_at": datetime(
                2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc
            ).isoformat(),
        }
        now = datetime(2026, 9, 3, 12, 16, 0, tzinfo=timezone.utc)
        assert is_expired(pending, now) is True

    def test_is_expired_false(self):
        pending = {
            "expires_at": datetime(
                2026, 9, 3, 12, 15, 0, tzinfo=timezone.utc
            ).isoformat(),
        }
        now = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
        assert is_expired(pending, now) is False

    def test_is_expired_no_field_returns_false(self):
        assert is_expired({}) is False

    def test_is_expired_invalid_format_returns_false(self):
        assert is_expired({"expires_at": "not-a-date"}) is False
