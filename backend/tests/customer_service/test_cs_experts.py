"""tests/customer_service/test_cs_experts.py — CS Expert 基础设施 + KnowledgeExpert 测试"""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from backend.customer_service.experts.base import (
    ExpertStatus,
    ExpertType,
    run_expert_safely,
)


class TestExpertStatus:
    def test_values(self):
        assert ExpertStatus.SUCCESS == "success"
        assert ExpertStatus.FAILED == "failed"
        assert ExpertStatus.TIMEOUT == "timeout"
        assert ExpertStatus.SKIPPED == "skipped"


class TestExpertType:
    def test_all_five_experts(self):
        names = {e.value for e in ExpertType}
        assert names == {"knowledge", "query", "action", "complaint", "handoff"}


class TestRunExpertSafely:
    def test_success(self):
        def fn(state):
            return {"response_draft": "hello", "status": "success"}

        result = run_expert_safely("knowledge", fn, {})
        assert result["expert"] == "knowledge"
        assert result["status"] == "success"
        assert result["response_draft"] == "hello"
        assert result["duration_ms"] >= 0

    def test_exception_returns_failed(self):
        def fn(state):
            raise ValueError("boom")

        result = run_expert_safely("query", fn, {})
        assert result["expert"] == "query"
        assert result["status"] == "failed"
        assert "boom" in result["error"]
        assert result["duration_ms"] >= 0

    def test_default_status_is_success(self):
        def fn(state):
            return {"response_draft": "ok"}

        result = run_expert_safely("action", fn, {})
        assert result["status"] == "success"

    @patch("backend.observability.metrics.record_cs_expert_result")
    def test_metrics_called_on_success(self, mock_record):
        def fn(state):
            return {"status": "success"}

        run_expert_safely("knowledge", fn, {})
        mock_record.assert_called_once_with("knowledge", "success")

    @patch("backend.observability.metrics.record_cs_expert_result")
    def test_metrics_called_on_failure(self, mock_record):
        def fn(state):
            raise RuntimeError("fail")

        run_expert_safely("knowledge", fn, {})
        mock_record.assert_called_once_with("knowledge", "failed")


@dataclass
class FakeDecision:
    value: str = "answered"


@dataclass
class FakeKnowledgeResult:
    answer: str = "test answer"
    decision: FakeDecision = None
    confidence: float = 0.92
    kb_ids: list = None
    suffix: str = ""
    error: str | None = None
    source_documents: list = None

    def __post_init__(self):
        if self.decision is None:
            self.decision = FakeDecision()
        if self.kb_ids is None:
            self.kb_ids = ["cs_faq"]
        if self.source_documents is None:
            self.source_documents = []


class TestKnowledgeExpert:
    @patch("backend.observability.metrics.record_cs_rag_status")
    @patch("backend.customer_service.knowledge.get_knowledge_service")
    def test_execute_knowledge_success(self, mock_get_svc, mock_record):
        fake_result = FakeKnowledgeResult(
            answer="退款政策是7天内可申请。",
            confidence=0.95,
            kb_ids=["cs_policy"],
        )
        mock_svc = MagicMock()
        mock_svc.answer.return_value = fake_result
        mock_get_svc.return_value = mock_svc

        from backend.customer_service.experts.knowledge import execute_knowledge

        result = execute_knowledge(
            user_message="怎么退款？",
            cs_route={"intent": "k_faq", "kb_ids": ["cs_policy"]},
            session_id="test-session",
        )

        assert result["status"] == "success"
        assert "退款" in result["response_draft"]
        assert result["data"]["confidence"] == 0.95
        mock_record.assert_called_once_with("hit")

    @patch("backend.observability.metrics.record_cs_rag_status")
    @patch("backend.customer_service.knowledge.get_knowledge_service")
    def test_execute_knowledge_empty_answer(self, mock_get_svc, mock_record):
        fake_result = FakeKnowledgeResult(answer="", confidence=0.3)
        mock_svc = MagicMock()
        mock_svc.answer.return_value = fake_result
        mock_get_svc.return_value = mock_svc

        from backend.customer_service.experts.knowledge import execute_knowledge

        result = execute_knowledge(
            user_message="unknown question",
            cs_route={"intent": "k_faq"},
            session_id="s1",
        )

        assert result["status"] == "success"
        assert "暂时无法找到" in result["response_draft"]
        mock_record.assert_called_once_with("miss")

    @patch("backend.observability.metrics.record_cs_rag_status")
    @patch("backend.customer_service.knowledge.get_knowledge_service")
    def test_execute_knowledge_with_evidence(self, mock_get_svc, mock_record):
        fake_result = FakeKnowledgeResult(
            answer="answer",
            source_documents=[
                {"source": "policy_doc_1", "score": 0.9},
                {"source": "faq_doc_2", "score": 0.8},
            ],
        )
        mock_svc = MagicMock()
        mock_svc.answer.return_value = fake_result
        mock_get_svc.return_value = mock_svc

        from backend.customer_service.experts.knowledge import execute_knowledge

        result = execute_knowledge(
            user_message="test",
            cs_route={"intent": "k_faq"},
            session_id="s1",
        )

        assert len(result["evidence"]) == 2
        assert result["evidence"][0]["source"] == "policy_doc_1"


class TestKnowledgeExpertNode:
    @patch("backend.observability.metrics.record_cs_rag_status")
    @patch("backend.customer_service.knowledge.get_knowledge_service")
    def test_node_writes_last_expert_result(self, mock_get_svc, mock_record):
        fake_result = FakeKnowledgeResult(answer="node test answer")
        mock_svc = MagicMock()
        mock_svc.answer.return_value = fake_result
        mock_get_svc.return_value = mock_svc

        from backend.customer_service.experts.knowledge import knowledge_expert_node

        state = {
            "user_message": "test question",
            "cs_route": {"intent": "k_faq", "kb_ids": ["cs_faq"]},
            "session_id": "s1",
            "expert_history": [],
        }

        output = knowledge_expert_node(state)
        assert "last_expert_result" in output
        assert output["last_expert_result"]["expert"] == "knowledge"
        assert len(output["expert_history"]) == 1
        assert output["expert_history"][0]["expert"] == "knowledge"


class TestQueryExpert:
    @patch("backend.customer_service.security.output_guard.get_output_guard")
    @patch("backend.customer_service.security.permission.PermissionChecker")
    @patch("backend.customer_service.service.order_service.get_order_service")
    def test_execute_query_order(self, mock_get_order, mock_perm, mock_guard):
        mock_perm.validate_user_identity.return_value = "user_123"
        mock_result = MagicMock()
        mock_result.orders = [
            {"order_no": "ORD-001", "status": "已发货", "total_amount": "99.00", "created_at": "2026-09-01"},
        ]
        mock_get_order.return_value.query_orders.return_value = mock_result

        from backend.customer_service.experts.query import execute_query

        result = execute_query(
            user_message="查看我的订单",
            cs_route={"intent": "t_order_status"},
            state={"user_id": "user_123"},
        )

        assert result["status"] == "success"
        assert result["expert"] == "query"
        assert "ORD-001" in result["response_draft"]
        assert result["data"]["user_id"] == "user_123"

    @patch("backend.customer_service.security.permission.PermissionChecker")
    def test_execute_query_no_orders(self, mock_perm):
        mock_perm.validate_user_identity.return_value = "user_456"

        from backend.customer_service.experts.query import execute_query

        mock_result = MagicMock()
        mock_result.orders = []

        with patch("backend.customer_service.service.order_service.get_order_service") as mock_get:
            mock_get.return_value.query_orders.return_value = mock_result

            result = execute_query(
                user_message="查看订单",
                cs_route={"intent": "t_order_status"},
                state={"user_id": "user_456"},
            )

        assert result["status"] == "success"
        assert "没有相关订单" in result["response_draft"]

    @patch("backend.customer_service.security.permission.PermissionChecker")
    def test_query_expert_node(self, mock_perm):
        mock_perm.validate_user_identity.return_value = "user_789"

        mock_result = MagicMock()
        mock_result.orders = []

        with patch("backend.customer_service.service.order_service.get_order_service") as mock_get:
            mock_get.return_value.query_orders.return_value = mock_result

            from backend.customer_service.experts.query import query_expert_node

            state = {
                "user_message": "查看订单",
                "cs_route": {"intent": "t_order_status"},
                "user_id": "user_789",
                "expert_history": [],
            }

            output = query_expert_node(state)
            assert output["last_expert_result"]["expert"] == "query"
            assert len(output["expert_history"]) == 1
            assert output["expert_history"][0]["expert"] == "query"


class TestComplaintExpert:
    @patch("backend.observability.metrics.record_cs_handoff")
    @patch("backend.customer_service.handoff_store.get_handoff_store")
    @patch("backend.customer_service.service.complaint_service.get_complaint_service")
    def test_execute_complaint_success(self, mock_get_svc, mock_get_store, mock_record):
        mock_detection = MagicMock()
        mock_detection.severity = "high"

        mock_ticket = MagicMock()
        mock_ticket.ticket_id = "CMP-001"

        mock_svc = MagicMock()
        mock_svc.detect.return_value = mock_detection
        mock_svc.create_ticket.return_value = mock_ticket
        mock_svc.build_comfort_response.return_value = "非常抱歉，我们已记录您的投诉。"
        mock_get_svc.return_value = mock_svc

        mock_store = MagicMock()
        mock_get_store.return_value = mock_store

        from backend.customer_service.experts.complaint import execute_complaint

        result = execute_complaint(
            user_message="我要投诉！服务质量太差了！",
            state={
                "user_id": "user_123",
                "session_id": "s1",
                "conversation_id": "conv_1",
            },
        )

        assert result["status"] == "success"
        assert result["expert"] == "complaint"
        assert "抱歉" in result["response_draft"]
        assert result["data"]["ticket_id"] == "CMP-001"
        assert result["data"]["handoff_state"] == "handoff_requested"
        mock_store.save.assert_called_once()
        mock_record.assert_called_once_with("complaint")

    @patch("backend.observability.metrics.record_cs_handoff")
    @patch("backend.customer_service.handoff_store.get_handoff_store")
    @patch("backend.customer_service.service.complaint_service.get_complaint_service")
    def test_complaint_expert_node(self, mock_get_svc, mock_get_store, mock_record):
        mock_detection = MagicMock()
        mock_detection.severity = "medium"
        mock_ticket = MagicMock()
        mock_ticket.ticket_id = "CMP-002"

        mock_svc = MagicMock()
        mock_svc.detect.return_value = mock_detection
        mock_svc.create_ticket.return_value = mock_ticket
        mock_svc.build_comfort_response.return_value = "安抚回复"
        mock_get_svc.return_value = mock_svc
        mock_get_store.return_value = MagicMock()

        from backend.customer_service.experts.complaint import complaint_expert_node

        state = {
            "user_message": "我要投诉",
            "user_id": "u1",
            "session_id": "s1",
            "conversation_id": "c1",
            "expert_history": [],
            "cs_audit_entries": [],
            "cs_context": {},
        }

        output = complaint_expert_node(state)
        assert output["last_expert_result"]["expert"] == "complaint"
        assert len(output["expert_history"]) == 1
        assert output["cs_context"]["handoff_state"] == "handoff_requested"
        assert len(output["cs_audit_entries"]) == 1


class TestHandoffExpert:
    @patch("backend.observability.metrics.record_cs_handoff")
    @patch("backend.customer_service.handoff.transition")
    @patch("backend.customer_service.handoff.detect_handoff_trigger")
    @patch("backend.customer_service.handoff_store.get_handoff_store")
    def test_execute_handoff_explicit(self, mock_get_store, mock_detect, mock_transition, mock_record):
        mock_trigger = MagicMock()
        mock_trigger.trigger_type.value = "explicit"
        mock_trigger.reason = "用户明确要求转人工"
        mock_detect.return_value = mock_trigger

        mock_store = MagicMock()
        mock_store.load.return_value = {"handoff_state": "ai_active"}
        mock_get_store.return_value = mock_store

        from backend.customer_service.experts.handoff import execute_handoff

        result = execute_handoff(
            user_message="转人工",
            state={
                "user_id": "user_123",
                "session_id": "s1",
                "conversation_id": "conv_1",
            },
        )

        assert result["status"] == "success"
        assert result["expert"] == "handoff"
        assert "转接人工客服" in result["response_draft"]
        assert result["data"]["trigger_type"] == "explicit"
        assert result["data"]["handoff_state"] == "handoff_requested"
        assert result["data"]["handling_mode"] == "human"
        assert result["data"]["ticket_id"].startswith("HANDOFF-")
        mock_store.save.assert_called_once()

    @patch("backend.observability.metrics.record_cs_handoff")
    @patch("backend.customer_service.handoff.transition")
    @patch("backend.customer_service.handoff.detect_handoff_trigger")
    @patch("backend.customer_service.handoff_store.get_handoff_store")
    def test_execute_handoff_auto_trigger(self, mock_get_store, mock_detect, mock_transition, mock_record):
        mock_detect.return_value = None
        mock_store = MagicMock()
        mock_store.load.return_value = None
        mock_get_store.return_value = mock_store

        from backend.customer_service.experts.handoff import execute_handoff

        result = execute_handoff(
            user_message="",
            state={
                "user_id": "u1",
                "session_id": "s1",
                "conversation_id": "c1",
            },
        )

        assert result["status"] == "success"
        assert result["data"]["trigger_type"] == "auto_trigger"

    @patch("backend.observability.metrics.record_cs_handoff")
    @patch("backend.customer_service.handoff.transition")
    @patch("backend.customer_service.handoff.detect_handoff_trigger")
    @patch("backend.customer_service.handoff_store.get_handoff_store")
    def test_handoff_expert_node(self, mock_get_store, mock_detect, mock_transition, mock_record):
        mock_trigger = MagicMock()
        mock_trigger.trigger_type.value = "explicit"
        mock_trigger.reason = "用户要求"
        mock_detect.return_value = mock_trigger

        mock_store = MagicMock()
        mock_store.load.return_value = {"handoff_state": "ai_active"}
        mock_get_store.return_value = mock_store

        from backend.customer_service.experts.handoff import handoff_expert_node

        state = {
            "user_message": "转人工",
            "user_id": "u1",
            "session_id": "s1",
            "conversation_id": "c1",
            "expert_history": [],
            "cs_audit_entries": [],
            "cs_context": {},
        }

        output = handoff_expert_node(state)
        assert output["last_expert_result"]["expert"] == "handoff"
        assert output["cs_context"]["handoff_state"] == "handoff_requested"
        assert output["cs_context"]["handling_mode"] == "human"


class TestActionExpert:
    @patch("backend.customer_service.confirmation_store.get_confirmation_store")
    @patch("backend.customer_service.security.permission.PermissionChecker")
    def test_execute_action_builds_proposal(self, mock_perm, mock_store_fn):
        mock_perm.validate_user_identity.return_value = "user_123"
        mock_store = MagicMock()
        mock_store.load.return_value = None
        mock_store_fn.return_value = mock_store

        mock_proposal = MagicMock()
        mock_proposal.action_type = "refund_request"
        mock_proposal.target_type = "order"
        mock_proposal.target_id = "ORD-001"
        mock_proposal.risk_level.value = "high"
        mock_proposal.proposal_text = "退款确认：订单 ORD-001"

        with patch("backend.customer_service.service.refund_service.get_refund_service") as mock_refund:
            mock_refund.return_value.build_refund_proposal.return_value = mock_proposal

            with patch("backend.customer_service.action.build_pending_action") as mock_build:
                mock_pending = {"action_type": "refund_request", "proposal_text": "退款确认"}
                mock_build.return_value = mock_pending

                from backend.customer_service.experts.action import execute_action

                result = execute_action(
                    user_message="我要退款",
                    cs_route={"intent": "as_refund", "metadata": {"order_id": "ORD-001"}},
                    state={"user_id": "user_123", "session_id": "s1"},
                )

        assert result["status"] == "success"
        assert result["expert"] == "action"
        assert "退款" in result["response_draft"]
        assert result["data"]["confirmation_state"] == "pending"
        mock_store.save.assert_called_once()

    @patch("backend.customer_service.confirmation_store.get_confirmation_store")
    @patch("backend.customer_service.security.permission.PermissionChecker")
    def test_execute_action_with_pending_confirmation(self, mock_perm, mock_store_fn):
        mock_perm.validate_user_identity.return_value = "user_123"
        mock_store = MagicMock()
        mock_store_fn.return_value = mock_store

        pending_action = {
            "action_type": "refund_request",
            "proposal_text": "退款确认",
            "target_type": "order",
            "target_id": "ORD-001",
            "risk_level": "high",
            "created_at": "2099-01-01T00:00:00Z",
            "ttl_seconds": 3600,
        }
        mock_store.load.return_value = pending_action

        with patch("backend.customer_service.confirmation.detect_confirmation_intent") as mock_detect:
            from backend.customer_service.confirmation import ConfirmationIntent
            mock_detect.return_value = ConfirmationIntent.CONFIRM

            with patch("backend.customer_service.confirmation.is_expired", return_value=False):
                with patch("backend.customer_service.confirmation.transition"):
                    with patch("backend.observability.metrics.record_cs_confirmation"):
                        with patch("backend.observability.metrics.record_cs_action"):
                            mock_record = MagicMock()
                            mock_record.action_id = "ACT-001"
                            mock_record.to_dict.return_value = {"id": "ACT-001"}

                            with patch("backend.customer_service.experts.action._simulate_execute", return_value=mock_record):
                                from backend.customer_service.experts.action import execute_action

                                result = execute_action(
                                    user_message="确认",
                                    cs_route={"intent": "as_refund"},
                                    state={"user_id": "user_123", "session_id": "s1"},
                                )

        assert result["status"] == "success"
        assert "成功" in result["response_draft"]
        assert result["data"]["action_result"]["status"] == "success"

    @patch("backend.customer_service.confirmation_store.get_confirmation_store")
    @patch("backend.customer_service.security.permission.PermissionChecker")
    def test_execute_action_cancel(self, mock_perm, mock_store_fn):
        mock_perm.validate_user_identity.return_value = "user_123"
        mock_store = MagicMock()
        mock_store_fn.return_value = mock_store

        pending_action = {
            "action_type": "refund_request",
            "proposal_text": "退款确认",
            "created_at": "2099-01-01T00:00:00Z",
            "ttl_seconds": 3600,
        }
        mock_store.load.return_value = pending_action

        with patch("backend.customer_service.confirmation.detect_confirmation_intent") as mock_detect:
            from backend.customer_service.confirmation import ConfirmationIntent
            mock_detect.return_value = ConfirmationIntent.CANCEL

            with patch("backend.customer_service.confirmation.is_expired", return_value=False):
                with patch("backend.customer_service.confirmation.transition"):
                    with patch("backend.observability.metrics.record_cs_confirmation"):
                        from backend.customer_service.experts.action import execute_action

                        result = execute_action(
                            user_message="取消",
                            cs_route={"intent": "as_refund"},
                            state={"user_id": "user_123", "session_id": "s1"},
                        )

        assert result["status"] == "success"
        assert "已取消" in result["response_draft"]
        mock_store.clear.assert_called_once()

    @patch("backend.customer_service.confirmation_store.get_confirmation_store")
    @patch("backend.customer_service.security.permission.PermissionChecker")
    def test_action_expert_node(self, mock_perm, mock_store_fn):
        mock_perm.validate_user_identity.return_value = "user_123"
        mock_store = MagicMock()
        mock_store.load.return_value = None
        mock_store_fn.return_value = mock_store

        mock_proposal = MagicMock()
        mock_proposal.action_type = "refund_request"
        mock_proposal.target_type = "order"
        mock_proposal.target_id = "ORD-001"
        mock_proposal.risk_level.value = "high"
        mock_proposal.proposal_text = "退款确认"

        with patch("backend.customer_service.service.refund_service.get_refund_service") as mock_refund:
            mock_refund.return_value.build_refund_proposal.return_value = mock_proposal

            with patch("backend.customer_service.action.build_pending_action") as mock_build:
                mock_build.return_value = {"action_type": "refund_request"}

                from backend.customer_service.experts.action import action_expert_node

                state = {
                    "user_message": "我要退款",
                    "cs_route": {"intent": "as_refund", "metadata": {"order_id": "ORD-001"}},
                    "user_id": "user_123",
                    "session_id": "s1",
                    "expert_history": [],
                    "cs_audit_entries": [],
                    "cs_context": {},
                }

                output = action_expert_node(state)
                assert output["last_expert_result"]["expert"] == "action"
                assert len(output["expert_history"]) == 1
                assert output["cs_context"]["confirmation_state"] == "pending"
