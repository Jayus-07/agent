"""test_business_action_node.py — cs_business_action 节点测试"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from backend.customer_service.action import ActionProposal, ActionType
from backend.customer_service.risk import RiskLevel

_PERM = "backend.customer_service.security.permission.PermissionChecker"
_GUARD = "backend.customer_service.security.output_guard.get_output_guard"
_STORE = "backend.customer_service.confirmation_store.get_confirmation_store"
_NODES = "backend.customer_service.graph.nodes"


def _make_state(
    intent="as_refund",
    question="帮我退款",
    user_id="user-1",
    session_id="sess-1",
    pending_action=None,
    cs_route_metadata=None,
):
    cs_context = {
        "authenticated_user_id": user_id,
        "session_id": session_id,
        "cs_route": {
            "intent": intent,
            "metadata": cs_route_metadata or {},
        },
    }
    if pending_action:
        cs_context["pending_action"] = pending_action
    return {
        "question": question,
        "cs_context": cs_context,
        "cs_audit_entries": [],
    }


def _fake_proposal(
    action_type=ActionType.REFUND_REQUEST,
    risk_level=RiskLevel.HIGH,
    target_type="order",
    target_id="42",
):
    return ActionProposal(
        action_type=action_type,
        target_type=target_type,
        target_id=target_id,
        risk_level=risk_level,
        proposal_text="## 测试操作\n\n确认执行？",
        before_state={"status": "paid"},
        after_state={"status": "done"},
    )


def _fake_pending(
    action_type=ActionType.REFUND_REQUEST,
    risk_level="high",
    expires_in_seconds=900,
):
    now = datetime.now(timezone.utc)
    return {
        "action_id": "test-action-id",
        "action_type": action_type,
        "target_type": "order",
        "target_id": "42",
        "risk_level": risk_level,
        "proposal_text": "## 退款申请\n\n确认？",
        "before_state": {"status": "paid"},
        "after_state": {"status": "refund_requested"},
        "requires_confirmation": True,
        "confirmation_state": "pending",
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=expires_in_seconds)).isoformat(),
    }


def _mock_guard():
    guard = MagicMock()
    guard.check.return_value = MagicMock(text="guarded text")
    return guard


class TestAuthenticationGate:
    @patch(_PERM)
    def test_unauthenticated_returns_login_prompt(self, mock_perm_cls):
        from backend.customer_service.errors import AuthenticationError
        from backend.customer_service.graph.nodes import cs_business_action

        mock_perm_cls.validate_user_identity.side_effect = AuthenticationError()

        state = _make_state()
        result = cs_business_action(state)
        assert "登录" in result["final_answer"]


class TestNewProposal:
    @patch(f"{_NODES}._build_proposal")
    @patch(_GUARD)
    @patch(_STORE)
    @patch(_PERM)
    def test_builds_proposal_and_saves_to_store(
        self, mock_perm_cls, mock_store_fn, mock_guard_fn, mock_build,
    ):
        from backend.customer_service.graph.nodes import cs_business_action

        mock_perm_cls.validate_user_identity.return_value = "user-1"
        mock_build.return_value = _fake_proposal()

        store = MagicMock()
        store.load.return_value = None
        mock_store_fn.return_value = store
        mock_guard_fn.return_value = _mock_guard()

        state = _make_state()
        result = cs_business_action(state)

        store.save.assert_called_once()
        assert result["cs_context"]["confirmation_state"] == "pending"
        assert "pending_action" in result["cs_context"]
        assert len(result["cs_audit_entries"]) == 1
        assert result["cs_audit_entries"][0]["result"] == "pending"


class TestPendingConfirmation:
    @patch(_GUARD)
    @patch(_STORE)
    @patch(_PERM)
    def test_expired_clears_and_returns_timeout(
        self, mock_perm_cls, mock_store_fn, mock_guard_fn,
    ):
        from backend.customer_service.graph.nodes import cs_business_action

        mock_perm_cls.validate_user_identity.return_value = "user-1"
        store = MagicMock()
        mock_store_fn.return_value = store

        pending = _fake_pending(expires_in_seconds=-1)
        state = _make_state(pending_action=pending)
        result = cs_business_action(state)

        assert "超时" in result["final_answer"]
        store.clear.assert_called_once()
        assert result["cs_context"]["confirmation_state"] == "expired"

    @patch(_GUARD)
    @patch(_STORE)
    @patch(_PERM)
    def test_cancel_clears_and_returns_cancelled(
        self, mock_perm_cls, mock_store_fn, mock_guard_fn,
    ):
        from backend.customer_service.graph.nodes import cs_business_action

        mock_perm_cls.validate_user_identity.return_value = "user-1"
        store = MagicMock()
        mock_store_fn.return_value = store

        pending = _fake_pending()
        state = _make_state(question="取消", pending_action=pending)
        result = cs_business_action(state)

        assert "取消" in result["final_answer"]
        store.clear.assert_called_once()
        assert result["cs_context"]["confirmation_state"] == "cancelled"

    @patch(f"{_NODES}._simulate_execute")
    @patch(_GUARD)
    @patch(_STORE)
    @patch(_PERM)
    def test_confirm_executes_and_succeeds(
        self, mock_perm_cls, mock_store_fn, mock_guard_fn, mock_sim,
    ):
        from backend.customer_service.graph.nodes import cs_business_action

        mock_perm_cls.validate_user_identity.return_value = "user-1"
        store = MagicMock()
        mock_store_fn.return_value = store

        record = MagicMock()
        record.action_id = "rec-1"
        record.to_dict.return_value = {"action_id": "rec-1", "status": "simulated"}
        mock_sim.return_value = record
        mock_guard_fn.return_value = _mock_guard()

        pending = _fake_pending()
        state = _make_state(question="确认", pending_action=pending)
        result = cs_business_action(state)

        mock_sim.assert_called_once()
        store.clear.assert_called_once()
        assert result["cs_context"]["confirmation_state"] == "success"
        assert result["cs_context"]["action_result"]["status"] == "success"
        assert len(result["cs_audit_entries"]) == 1
        assert result["cs_audit_entries"][0]["result"] == "success"

    @patch(_GUARD)
    @patch(_STORE)
    @patch(_PERM)
    def test_unrelated_text_reprompts(
        self, mock_perm_cls, mock_store_fn, mock_guard_fn,
    ):
        from backend.customer_service.graph.nodes import cs_business_action

        mock_perm_cls.validate_user_identity.return_value = "user-1"
        store = MagicMock()
        mock_store_fn.return_value = store

        guard = MagicMock()
        guard.check.return_value = MagicMock(text="re-prompt text")
        mock_guard_fn.return_value = guard

        pending = _fake_pending()
        state = _make_state(question="我想查一下物流", pending_action=pending)
        cs_business_action(state)

        store.clear.assert_not_called()
        guard_input = guard.check.call_args[0][0]
        assert "待确认" in guard_input

    @patch(f"{_NODES}._simulate_execute")
    @patch(_GUARD)
    @patch(_STORE)
    @patch(_PERM)
    def test_execution_failure_sets_failed(
        self, mock_perm_cls, mock_store_fn, mock_guard_fn, mock_sim,
    ):
        from backend.customer_service.errors import ActionExecutionError
        from backend.customer_service.graph.nodes import cs_business_action

        mock_perm_cls.validate_user_identity.return_value = "user-1"
        store = MagicMock()
        mock_store_fn.return_value = store
        mock_sim.side_effect = ActionExecutionError("db error")

        pending = _fake_pending()
        state = _make_state(question="确认", pending_action=pending)
        result = cs_business_action(state)

        assert result["cs_context"]["confirmation_state"] == "failed"
        store.clear.assert_called_once()
        assert len(result["cs_audit_entries"]) == 1
        assert result["cs_audit_entries"][0]["result"] == "failure"


class TestErrorHandling:
    @patch(_PERM)
    def test_order_not_found(self, mock_perm_cls):
        from backend.customer_service.graph.nodes import cs_business_action

        mock_perm_cls.validate_user_identity.return_value = "user-1"

        with patch(f"{_NODES}._build_proposal") as mock_build:
            from backend.customer_service.errors import OrderNotFoundError
            mock_build.side_effect = OrderNotFoundError()
            state = _make_state()
            result = cs_business_action(state)
            assert "未找到" in result["final_answer"]

    @patch(_PERM)
    def test_not_eligible_shows_user_message(self, mock_perm_cls):
        from backend.customer_service.graph.nodes import cs_business_action

        mock_perm_cls.validate_user_identity.return_value = "user-1"

        with patch(f"{_NODES}._build_proposal") as mock_build:
            from backend.customer_service.errors import OrderNotEligibleError
            mock_build.side_effect = OrderNotEligibleError("订单已退款")
            state = _make_state()
            result = cs_business_action(state)
            assert "订单已退款" in result["final_answer"]
