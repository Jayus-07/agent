"""tests/customer_service/test_cs_graph_builder.py — CS Graph 拓扑编译测试"""
from __future__ import annotations

from unittest.mock import patch

from backend.customer_service.graph_builder import build_cs_graph


class TestBuildCSGraph:
    def test_compiles_without_error(self):
        graph = build_cs_graph()
        assert graph is not None

    @patch(
        "backend.customer_service.graph_builder.get_state_transition_service"
    )
    def test_invoke_knowledge_path(self, mock_sts):
        mock_sts.return_value.load_snapshot.return_value = {
            "conversation_status": "open",
            "handling_mode": "ai",
            "handoff_state": "ai_active",
            "confirmation_state": "not_required",
            "pending_action": None,
        }

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
            from unittest.mock import MagicMock

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

    @patch(
        "backend.customer_service.graph_builder.get_state_transition_service"
    )
    def test_low_confidence_finishes_without_expert(self, mock_sts):
        mock_sts.return_value.load_snapshot.return_value = {
            "conversation_status": "open",
            "handling_mode": "ai",
            "handoff_state": "ai_active",
            "confirmation_state": "not_required",
            "pending_action": None,
        }

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
        assert result["expert_history"] == []
