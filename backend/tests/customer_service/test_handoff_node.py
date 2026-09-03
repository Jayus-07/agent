"""test_handoff_node.py — cs_handoff + cs_handoff_intercept 节点测试"""
from unittest.mock import MagicMock, patch

import pytest

from backend.customer_service.graph.nodes import cs_handoff, cs_handoff_intercept

_GUARD = "backend.customer_service.security.output_guard.get_output_guard"
_STORE = "backend.customer_service.handoff_store.get_handoff_store"


@pytest.fixture
def base_state():
    return {
        "question": "帮我转人工",
        "cs_context": {
            "authenticated_user_id": "user1",
            "session_id": "session1",
            "conversation_id": "conv1",
        },
        "cs_audit_entries": [],
    }


class TestCSHandoff:

    @patch(_STORE)
    @patch(_GUARD)
    def test_handoff_sets_state_and_creates_ticket(self, mock_guard_fn, mock_store_fn, base_state):
        mock_store = MagicMock()
        mock_store.load.return_value = None
        mock_store_fn.return_value = mock_store

        mock_guard = MagicMock()
        mock_guard.check.return_value = MagicMock(text="已为您转接人工客服，请稍候。")
        mock_guard_fn.return_value = mock_guard

        result = cs_handoff(base_state)

        assert "final_answer" in result
        assert result["cs_context"]["handoff_state"] == "handoff_requested"
        mock_store.save.assert_called_once()
        save_data = mock_store.save.call_args[0][2]
        assert save_data["ticket_id"].startswith("HANDOFF-")
        assert save_data["handoff_state"] == "handoff_requested"

    @patch(_STORE)
    @patch(_GUARD)
    def test_handoff_audit_entry(self, mock_guard_fn, mock_store_fn, base_state):
        mock_store = MagicMock()
        mock_store.load.return_value = None
        mock_store_fn.return_value = mock_store

        mock_guard = MagicMock()
        mock_guard.check.return_value = MagicMock(text="已转接")
        mock_guard_fn.return_value = mock_guard

        result = cs_handoff(base_state)

        assert len(result["cs_audit_entries"]) == 1
        audit = result["cs_audit_entries"][0]
        assert audit["action_type"] == "handoff_requested"
        assert audit["result"] == "success"
        assert audit["target_type"] == "handoff"

    @patch(_STORE)
    @patch(_GUARD)
    def test_handoff_explicit_trigger_detected(self, mock_guard_fn, mock_store_fn, base_state):
        mock_store = MagicMock()
        mock_store.load.return_value = None
        mock_store_fn.return_value = mock_store

        mock_guard = MagicMock()
        mock_guard.check.return_value = MagicMock(text="已转接")
        mock_guard_fn.return_value = mock_guard

        cs_handoff(base_state)

        save_data = mock_store.save.call_args[0][2]
        assert save_data["trigger_type"] == "explicit_request"

    @patch(_STORE)
    @patch(_GUARD)
    def test_handoff_auto_trigger_when_no_explicit(self, mock_guard_fn, mock_store_fn):
        state = {
            "question": "我的订单怎么了",
            "cs_context": {
                "authenticated_user_id": "user1",
                "session_id": "session1",
            },
            "cs_audit_entries": [],
        }

        mock_store = MagicMock()
        mock_store.load.return_value = None
        mock_store_fn.return_value = mock_store

        mock_guard = MagicMock()
        mock_guard.check.return_value = MagicMock(text="已转接")
        mock_guard_fn.return_value = mock_guard

        cs_handoff(state)

        save_data = mock_store.save.call_args[0][2]
        assert save_data["trigger_type"] == "auto_trigger"

    def test_handoff_handles_exception_gracefully(self, base_state):
        with patch(_STORE, side_effect=Exception("fail")):
            result = cs_handoff(base_state)
            assert "final_answer" in result
            assert "不可用" in result["final_answer"] or "重试" in result["final_answer"]

    @patch(_STORE)
    @patch(_GUARD)
    def test_handoff_from_existing_ai_active(self, mock_guard_fn, mock_store_fn, base_state):
        mock_store = MagicMock()
        mock_store.load.return_value = {"handoff_state": "ai_active"}
        mock_store_fn.return_value = mock_store

        mock_guard = MagicMock()
        mock_guard.check.return_value = MagicMock(text="已转接")
        mock_guard_fn.return_value = mock_guard

        result = cs_handoff(base_state)

        assert result["cs_context"]["handoff_state"] == "handoff_requested"


class TestCSHandoffIntercept:

    @patch(_GUARD)
    def test_human_active_message(self, mock_guard_fn):
        state = {
            "cs_context": {"handoff_state": "human_active"},
        }
        mock_guard = MagicMock()
        mock_guard.check.return_value = MagicMock(text="当前由人工客服为您服务")
        mock_guard_fn.return_value = mock_guard

        result = cs_handoff_intercept(state)
        assert "人工客服" in result["final_answer"]

    @patch(_GUARD)
    def test_handoff_requested_message(self, mock_guard_fn):
        state = {
            "cs_context": {"handoff_state": "handoff_requested"},
        }
        mock_guard = MagicMock()
        mock_guard.check.return_value = MagicMock(text="正在为您转接人工客服")
        mock_guard_fn.return_value = mock_guard

        result = cs_handoff_intercept(state)
        assert "转接" in result["final_answer"] or "人工" in result["final_answer"]

    @patch(_GUARD)
    def test_waiting_human_message(self, mock_guard_fn):
        state = {
            "cs_context": {"handoff_state": "waiting_human"},
        }
        mock_guard = MagicMock()
        mock_guard.check.return_value = MagicMock(text="正在转接中")
        mock_guard_fn.return_value = mock_guard

        result = cs_handoff_intercept(state)
        assert "final_answer" in result

    @patch(_GUARD)
    def test_invalid_state_defaults_to_ai_active(self, mock_guard_fn):
        state = {
            "cs_context": {"handoff_state": "invalid_state"},
        }
        mock_guard = MagicMock()
        mock_guard.check.return_value = MagicMock(text="服务状态特殊")
        mock_guard_fn.return_value = mock_guard

        result = cs_handoff_intercept(state)
        assert "final_answer" in result

    @patch(_GUARD)
    def test_missing_handoff_state_defaults(self, mock_guard_fn):
        state = {"cs_context": {}}
        mock_guard = MagicMock()
        mock_guard.check.return_value = MagicMock(text="服务状态特殊")
        mock_guard_fn.return_value = mock_guard

        result = cs_handoff_intercept(state)
        assert "final_answer" in result
