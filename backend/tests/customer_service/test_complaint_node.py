"""test_complaint_node.py — cs_complaint 节点测试"""
from unittest.mock import MagicMock, patch

import pytest

from backend.customer_service.graph.nodes import cs_complaint

_COMPLAINT_SVC = "backend.customer_service.service.complaint_service.get_complaint_service"
_GUARD = "backend.customer_service.security.output_guard.get_output_guard"
_STORE = "backend.customer_service.handoff_store.get_handoff_store"


@pytest.fixture
def base_state():
    return {
        "question": "你们的服务太差了，我要投诉",
        "cs_context": {
            "authenticated_user_id": "user1",
            "session_id": "session1",
            "conversation_id": "conv1",
        },
        "cs_audit_entries": [],
    }


class TestCSComplaint:

    @patch(_STORE)
    @patch(_GUARD)
    @patch(_COMPLAINT_SVC)
    def test_complaint_creates_ticket_and_triggers_handoff(
        self, mock_svc_fn, mock_guard_fn, mock_store_fn, base_state
    ):
        from backend.customer_service.service.complaint_service import (
            ComplaintDetection,
            ComplaintTicket,
        )

        mock_service = MagicMock()
        mock_service.detect.return_value = ComplaintDetection(
            is_complaint=True, severity="medium", matched_patterns=["pattern1"]
        )
        ticket = ComplaintTicket(
            ticket_id="COMPLAINT-TEST0001",
            user_id="user1",
            conversation_id="conv1",
            severity="medium",
            summary="test",
        )
        mock_service.create_ticket.return_value = ticket
        mock_service.simulate_execute.return_value = {"executed": True}
        mock_service.build_comfort_response.return_value = "很抱歉给您带来不便 (工单号: COMPLAINT-TEST0001)"
        mock_svc_fn.return_value = mock_service

        mock_guard = MagicMock()
        mock_guard.check.return_value = MagicMock(text="很抱歉给您带来不便")
        mock_guard_fn.return_value = mock_guard

        mock_store = MagicMock()
        mock_store_fn.return_value = mock_store

        result = cs_complaint(base_state)

        assert "final_answer" in result
        assert result["cs_context"]["handoff_state"] == "handoff_requested"
        mock_store.save.assert_called_once()
        assert len(result["cs_audit_entries"]) == 1
        assert result["cs_audit_entries"][0]["action_type"] == "complaint_ticket_created"

    @patch(_STORE)
    @patch(_GUARD)
    @patch(_COMPLAINT_SVC)
    def test_complaint_sets_handoff_state_in_context(
        self, mock_svc_fn, mock_guard_fn, mock_store_fn, base_state
    ):
        from backend.customer_service.service.complaint_service import (
            ComplaintDetection,
            ComplaintTicket,
        )

        mock_service = MagicMock()
        mock_service.detect.return_value = ComplaintDetection(
            is_complaint=True, severity="high", matched_patterns=["p1", "p2"]
        )
        ticket = ComplaintTicket(
            ticket_id="COMPLAINT-HIGH001",
            user_id="user1",
            conversation_id="conv1",
            severity="high",
            summary="test",
        )
        mock_service.create_ticket.return_value = ticket
        mock_service.simulate_execute.return_value = {"executed": True}
        mock_service.build_comfort_response.return_value = "非常抱歉 (工单号: COMPLAINT-HIGH001)"
        mock_svc_fn.return_value = mock_service

        mock_guard = MagicMock()
        mock_guard.check.return_value = MagicMock(text="非常抱歉")
        mock_guard_fn.return_value = mock_guard

        mock_store = MagicMock()
        mock_store_fn.return_value = mock_store

        cs_complaint(base_state)

        save_call_args = mock_store.save.call_args
        handoff_data = save_call_args[0][2]
        assert handoff_data["handoff_state"] == "handoff_requested"
        assert handoff_data["trigger_type"] == "complaint_escalation"

    def test_complaint_handles_exception_gracefully(self, base_state):
        with patch(_COMPLAINT_SVC, side_effect=Exception("fail")):
            result = cs_complaint(base_state)
            assert "final_answer" in result
            assert "抱歉" in result["final_answer"]

    @patch(_STORE)
    @patch(_GUARD)
    @patch(_COMPLAINT_SVC)
    def test_complaint_audit_entry_contains_ticket_id(
        self, mock_svc_fn, mock_guard_fn, mock_store_fn, base_state
    ):
        from backend.customer_service.service.complaint_service import (
            ComplaintDetection,
            ComplaintTicket,
        )

        mock_service = MagicMock()
        mock_service.detect.return_value = ComplaintDetection(
            is_complaint=True, severity="medium", matched_patterns=["p1"]
        )
        ticket = ComplaintTicket(
            ticket_id="COMPLAINT-AUDIT01",
            user_id="user1",
            conversation_id="conv1",
            severity="medium",
            summary="test",
        )
        mock_service.create_ticket.return_value = ticket
        mock_service.simulate_execute.return_value = {"executed": True}
        mock_service.build_comfort_response.return_value = "抱歉"
        mock_svc_fn.return_value = mock_service

        mock_guard = MagicMock()
        mock_guard.check.return_value = MagicMock(text="抱歉")
        mock_guard_fn.return_value = mock_guard

        mock_store = MagicMock()
        mock_store_fn.return_value = mock_store

        result = cs_complaint(base_state)

        audit = result["cs_audit_entries"][0]
        assert audit["target_id"] == "COMPLAINT-AUDIT01"
        assert audit["result"] == "success"
