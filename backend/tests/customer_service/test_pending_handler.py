"""tests/customer_service/test_pending_handler.py — Phase 5: Pending Handler 测试

验证:
  1. 无 pending_action → 直通 cs_supervisor
  2. pending 已过期 → 清理 + 超时回复 → cs_reporter
  3. 用户确认 → 执行成功 → cs_reporter
  4. 用户确认 → 执行失败 → cs_reporter
  5. 用户取消 → 清理 + 取消回复 → cs_reporter
  6. 用户意图不明确 → 重新追问 → cs_reporter
  7. confirmation_state 非 pending → 直通 cs_supervisor
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from langgraph.types import Command

from backend.customer_service.pending_handler import cs_pending_handler_node


def _pending_action(**overrides) -> dict:
    base = {
        "action_type": "refund_request",
        "target_type": "order",
        "target_id": "ORD-001",
        "risk_level": "high",
        "proposal_text": "确认退款 ¥99.00 到订单 ORD-001？",
        "confirmation_state": "pending",
        "expires_at": "2099-12-31T23:59:59+00:00",
    }
    base.update(overrides)
    return base


def _state(**overrides) -> dict:
    base = {
        "user_id": "u1",
        "session_id": "s1",
        "conversation_id": "c1",
        "user_message": "确认",
        "pending_action": None,
        "confirmation_state": "not_required",
    }
    base.update(overrides)
    return base


class TestPendingHandlerPassthrough:
    def test_no_pending_action_routes_to_supervisor(self):
        cmd = cs_pending_handler_node(_state())
        assert isinstance(cmd, Command)
        assert cmd.goto == "cs_supervisor"

    def test_non_pending_confirmation_state_routes_to_supervisor(self):
        cmd = cs_pending_handler_node(_state(
            pending_action=_pending_action(),
            confirmation_state="success",
        ))
        assert cmd.goto == "cs_supervisor"

    def test_empty_pending_action_routes_to_supervisor(self):
        cmd = cs_pending_handler_node(_state(pending_action={}))
        assert cmd.goto == "cs_supervisor"


class TestPendingHandlerExpiry:
    @patch("backend.customer_service.confirmation.is_expired", return_value=True)
    @patch("backend.customer_service.confirmation_store.get_confirmation_store")
    @patch("backend.observability.metrics.record_cs_confirmation")
    def test_expired_pending_routes_to_reporter(
        self, mock_metric, mock_store_fn, mock_expired,
    ):
        mock_store = MagicMock()
        mock_store_fn.return_value = mock_store

        cmd = cs_pending_handler_node(_state(
            pending_action=_pending_action(),
            confirmation_state="pending",
        ))

        assert cmd.goto == "cs_reporter"
        assert cmd.update["confirmation_state"] == "expired"
        mock_store.clear.assert_called_once_with("u1", "s1")


class TestPendingHandlerConfirm:
    @patch("backend.customer_service.confirmation.is_expired", return_value=False)
    @patch("backend.customer_service.experts.action._simulate_execute")
    @patch("backend.customer_service.confirmation_store.get_confirmation_store")
    @patch("backend.observability.metrics.record_cs_confirmation")
    @patch("backend.observability.metrics.record_cs_action")
    def test_confirm_success(
        self, mock_action, mock_confirm, mock_store_fn, mock_execute, mock_expired,
    ):
        mock_store = MagicMock()
        mock_store_fn.return_value = mock_store

        mock_record = MagicMock()
        mock_record.to_dict.return_value = {"action_id": "ACT-001"}
        mock_record.action_id = "ACT-001"
        mock_execute.return_value = mock_record

        cmd = cs_pending_handler_node(_state(
            pending_action=_pending_action(),
            confirmation_state="pending",
            user_message="确认",
        ))

        assert cmd.goto == "cs_reporter"
        assert cmd.update["confirmation_state"] == "success"
        assert "cs_action_result" in cmd.update
        mock_store.clear.assert_called_once()

    @patch("backend.customer_service.confirmation.is_expired", return_value=False)
    @patch("backend.customer_service.experts.action._simulate_execute")
    @patch("backend.customer_service.confirmation_store.get_confirmation_store")
    @patch("backend.observability.metrics.record_cs_confirmation")
    @patch("backend.observability.metrics.record_cs_action")
    def test_confirm_failure(
        self, mock_action, mock_confirm, mock_store_fn, mock_execute, mock_expired,
    ):
        mock_store = MagicMock()
        mock_store_fn.return_value = mock_store
        mock_execute.side_effect = RuntimeError("DB down")

        cmd = cs_pending_handler_node(_state(
            pending_action=_pending_action(),
            confirmation_state="pending",
            user_message="确定",
        ))

        assert cmd.goto == "cs_reporter"
        assert cmd.update["confirmation_state"] == "failed"
        mock_store.clear.assert_called_once()


class TestPendingHandlerCancel:
    @patch("backend.customer_service.confirmation.is_expired", return_value=False)
    @patch("backend.customer_service.confirmation_store.get_confirmation_store")
    @patch("backend.observability.metrics.record_cs_confirmation")
    def test_cancel_routes_to_reporter(self, mock_metric, mock_store_fn, mock_expired):
        mock_store = MagicMock()
        mock_store_fn.return_value = mock_store

        cmd = cs_pending_handler_node(_state(
            pending_action=_pending_action(),
            confirmation_state="pending",
            user_message="取消",
        ))

        assert cmd.goto == "cs_reporter"
        assert cmd.update["confirmation_state"] == "cancelled"
        mock_store.clear.assert_called_once_with("u1", "s1")


class TestPendingHandlerUnclear:
    @patch("backend.customer_service.confirmation.is_expired", return_value=False)
    def test_unclear_intent_reprompts(self, mock_expired):
        cmd = cs_pending_handler_node(_state(
            pending_action=_pending_action(),
            confirmation_state="pending",
            user_message="今天天气怎么样",
        ))

        assert cmd.goto == "cs_reporter"
        draft = cmd.update["last_expert_result"]["response_draft"]
        assert "确认" in draft
        assert "取消" in draft
