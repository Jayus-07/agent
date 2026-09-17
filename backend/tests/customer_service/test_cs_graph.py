"""tests/customer_service/test_cs_graph.py — Phase 4: CS Graph Command 路由测试

验证:
  1. cs_supervisor_node 返回 Command(goto=..., update={...})
  2. CS Graph 编译成功（无 conditional edges）
  3. Command 路由到正确的 expert / reporter
  4. 端到端 invoke: supervisor → expert → supervisor loop → reporter
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from langgraph.types import Command

from backend.customer_service.graph_state import (
    CS_ACTION_EXPERT,
    CS_COMPLAINT_EXPERT,
    CS_KNOWLEDGE_EXPERT,
    CS_QUERY_EXPERT,
    CS_REPORTER,
)
from backend.customer_service.supervisor import cs_supervisor_node


def _state(**overrides) -> dict:
    base = {
        "cs_route": {
            "domain": "KNOWLEDGE",
            "route_path": "knowledge_query",
            "intent": "k_faq",
            "confidence": 0.90,
        },
        "handoff_state": "ai_active",
        "confirmation_state": "not_required",
        "expert_loop_count": 0,
        "expert_history": [],
        "user_message": "test question",
    }
    base.update(overrides)
    return base


class TestSupervisorReturnsCommand:
    def test_returns_command_type(self):
        cmd = cs_supervisor_node(_state())
        assert isinstance(cmd, Command)

    def test_knowledge_routes_to_knowledge_expert(self):
        cmd = cs_supervisor_node(_state())
        assert cmd.goto == CS_KNOWLEDGE_EXPERT
        assert cmd.update["current_expert"] == "knowledge"
        assert cmd.update["expert_loop_count"] == 1

    def test_business_query_routes_to_query_expert(self):
        s = _state(cs_route={
            "domain": "TRANSACTION",
            "route_path": "business_query",
            "confidence": 0.85,
        })
        cmd = cs_supervisor_node(s)
        assert cmd.goto == CS_QUERY_EXPERT
        assert cmd.update["current_expert"] == "query"

    def test_business_action_routes_to_action_expert(self):
        s = _state(cs_route={
            "domain": "AFTER_SALES",
            "route_path": "business_action",
            "confidence": 0.85,
        })
        cmd = cs_supervisor_node(s)
        assert cmd.goto == CS_ACTION_EXPERT
        assert cmd.update["current_expert"] == "action"

    def test_complaint_routes_to_complaint_expert(self):
        s = _state(cs_route={
            "domain": "COMPLAINT",
            "route_path": "complaint_flow",
            "confidence": 0.80,
        })
        cmd = cs_supervisor_node(s)
        assert cmd.goto == CS_COMPLAINT_EXPERT
        assert cmd.update["current_expert"] == "complaint"

    def test_handoff_intercept_routes_to_reporter(self):
        s = _state(handoff_state="handoff_requested")
        cmd = cs_supervisor_node(s)
        assert cmd.goto == CS_REPORTER

    def test_finish_routes_to_reporter(self):
        s = _state(expert_loop_count=100)
        cmd = cs_supervisor_node(s)
        assert cmd.goto == CS_REPORTER

    def test_pending_routes_to_reporter(self):
        s = _state(confirmation_state="pending")
        cmd = cs_supervisor_node(s)
        assert cmd.goto == CS_REPORTER

    def test_update_contains_supervisor_decision(self):
        cmd = cs_supervisor_node(_state())
        decision = cmd.update["supervisor_decision"]
        assert "next_action" in decision
        assert "decision_layer" in decision
        assert "reason" in decision

    def test_expert_loop_count_increments(self):
        s = _state(expert_loop_count=3)
        cmd = cs_supervisor_node(s)
        assert cmd.update["expert_loop_count"] == 4


class TestCSGraphCompiles:
    def test_build_cs_graph_compiles(self):
        from backend.customer_service.graph_builder import build_cs_graph
        graph = build_cs_graph()
        assert graph is not None

    @patch("backend.customer_service.graph_builder.get_state_transition_service")
    def test_invoke_knowledge_path_with_command_routing(self, mock_sts):
        """端到端: state_loader → supervisor → Command → knowledge_expert → supervisor → reporter"""
        mock_sts.return_value.load_snapshot.return_value = {
            "conversation_status": "open",
            "handling_mode": "ai",
            "handoff_state": "ai_active",
            "confirmation_state": "not_required",
            "pending_action": None,
        }

        from backend.customer_service.graph_builder import build_cs_graph
        graph = build_cs_graph()

        cs_input = {
            "user_message": "怎么退款？",
            "user_id": "u1",
            "session_id": "s1",
            "conversation_id": "c1",
            "cs_route": {
                "domain": "KNOWLEDGE",
                "route_path": "knowledge_query",
                "intent": "k_faq",
                "confidence": 0.90,
                "kb_ids": ["cs_faq"],
            },
            "supervisor_decision": {},
            "expert_history": [],
            "last_expert_result": {},
            "current_expert": "",
            "expert_loop_count": 0,
            "conversation_status": "",
            "handling_mode": "",
            "handoff_state": "",
            "confirmation_state": "",
            "pending_action": None,
            "final_answer": "",
            "cs_context": {},
            "cs_audit_entries": [],
            "cs_action_result": {},
        }

        with patch(
            "backend.customer_service.knowledge.get_knowledge_service"
        ) as mock_ks:
            from dataclasses import dataclass

            @dataclass
            class FakeDecision:
                value: str = "answered"

            fake_result = MagicMock()
            fake_result.answer = "退款政策是7天内可申请。"
            fake_result.decision = FakeDecision()
            fake_result.confidence = 0.92
            fake_result.kb_ids = ["cs_faq"]
            fake_result.source_documents = []

            mock_svc = MagicMock()
            mock_svc.answer.return_value = fake_result
            mock_ks.return_value = mock_svc

            result = graph.invoke(cs_input)

        assert result["final_answer"]
        assert "退款" in result["final_answer"]
        assert len(result["expert_history"]) >= 1
        assert result["expert_history"][0]["expert"] == "knowledge"

    @patch("backend.customer_service.graph_builder.get_state_transition_service")
    def test_low_confidence_knowledge_enters_expert(self, mock_sts):
        """P2.1（audit #156）：低置信但知识类（低风险无权限）→ 放行 knowledge expert。

        旧行为直接 finish（标尺错位误拒），知识库明明可答却拿泛化兜底。
        """
        mock_sts.return_value.load_snapshot.return_value = {
            "conversation_status": "open",
            "handling_mode": "ai",
            "handoff_state": "ai_active",
            "confirmation_state": "not_required",
            "pending_action": None,
        }

        from backend.customer_service.graph_builder import build_cs_graph
        graph = build_cs_graph()

        cs_input = {
            "user_message": "ambiguous",
            "user_id": "u1",
            "session_id": "s1",
            "conversation_id": "c1",
            "cs_route": {
                "domain": "KNOWLEDGE",
                "route_path": "knowledge_query",
                "intent": "unknown",
                "confidence": 0.20,
            },
            "supervisor_decision": {},
            "expert_history": [],
            "last_expert_result": {},
            "current_expert": "",
            "expert_loop_count": 0,
            "conversation_status": "",
            "handling_mode": "",
            "handoff_state": "",
            "confirmation_state": "",
            "pending_action": None,
            "final_answer": "",
            "cs_context": {},
            "cs_audit_entries": [],
            "cs_action_result": {},
        }

        result = graph.invoke(cs_input)

        assert result["final_answer"]
        assert [h["expert"] for h in result["expert_history"]] == ["knowledge"]

    @patch("backend.customer_service.graph_builder.get_state_transition_service")
    def test_low_confidence_action_skips_expert(self, mock_sts):
        """低置信 + 动作类（非知识、有风险）→ supervisor 直接 → reporter。"""
        mock_sts.return_value.load_snapshot.return_value = {
            "conversation_status": "open",
            "handling_mode": "ai",
            "handoff_state": "ai_active",
            "confirmation_state": "not_required",
            "pending_action": None,
        }

        from backend.customer_service.graph_builder import build_cs_graph
        graph = build_cs_graph()

        cs_input = {
            "user_message": "ambiguous",
            "user_id": "u1",
            "session_id": "s1",
            "conversation_id": "c1",
            "cs_route": {
                "domain": "AFTER_SALES",
                "route_path": "business_action",
                "intent": "unknown",
                "confidence": 0.20,
            },
            "supervisor_decision": {},
            "expert_history": [],
            "last_expert_result": {},
            "current_expert": "",
            "expert_loop_count": 0,
            "conversation_status": "",
            "handling_mode": "",
            "handoff_state": "",
            "confirmation_state": "",
            "pending_action": None,
            "final_answer": "",
            "cs_context": {},
            "cs_audit_entries": [],
            "cs_action_result": {},
        }

        result = graph.invoke(cs_input)

        assert result["final_answer"]
        assert result["expert_history"] == []
