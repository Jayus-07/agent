"""test_cs_graph_skeleton.py — CS Graph 骨架测试

Phase 0.12: 验证 CS Graph 可编译、可 invoke、输出契约正确。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.customer_service.graph_builder import build_cs_graph
from backend.customer_service.graph_state import new_cs_graph_input
from backend.customer_service.models.graph_result import (
    CSGraphResult,
    build_cs_graph_result,
    _derive_status,
    _build_answer_meta,
)


class TestBuildCsGraphResult:
    def test_success_when_has_final_answer(self):
        state = {
            "final_answer": "你好",
            "conversation_id": "c1",
            "handoff_state": "ai_active",
            "confirmation_state": "not_required",
            "cs_action_result": {},
            "cs_audit_entries": [],
            "last_expert_result": {},
        }
        result = build_cs_graph_result(state)
        assert result["status"] == "success"
        assert result["final_answer"] == "你好"
        assert result["conversation_id"] == "c1"

    def test_needs_handoff_when_handoff_not_ai_active(self):
        state = {
            "final_answer": "正在转接...",
            "conversation_id": "c1",
            "handoff_state": "handoff_requested",
            "cs_action_result": {},
            "cs_audit_entries": [],
            "last_expert_result": {},
        }
        result = build_cs_graph_result(state)
        assert result["status"] == "needs_handoff"
        assert result["handoff_state"] == "handoff_requested"

    def test_failed_when_no_answer(self):
        state = {
            "final_answer": "",
            "conversation_id": "c1",
            "handoff_state": "ai_active",
            "cs_action_result": {},
            "cs_audit_entries": [],
            "last_expert_result": {},
        }
        result = build_cs_graph_result(state)
        assert result["status"] == "failed"

    def test_answer_meta_includes_evidence(self):
        state = {
            "final_answer": "回答",
            "conversation_id": "c1",
            "handoff_state": "ai_active",
            "cs_action_result": {},
            "cs_audit_entries": [],
            "last_expert_result": {"evidence": [{"doc_id": "d1", "text": "引用"}]},
        }
        result = build_cs_graph_result(state)
        assert result["answer_meta"] is not None
        assert result["answer_meta"]["evidence"][0]["doc_id"] == "d1"

    def test_answer_meta_none_when_no_evidence(self):
        state = {
            "final_answer": "回答",
            "conversation_id": "c1",
            "handoff_state": "ai_active",
            "cs_action_result": {},
            "cs_audit_entries": [],
            "last_expert_result": {},
        }
        result = build_cs_graph_result(state)
        assert result["answer_meta"] is None


class TestDeriveStatus:
    def test_empty_handoff_state_treated_as_ai_active(self):
        assert _derive_status({"handoff_state": "", "final_answer": "x"}) == "success"

    def test_human_active_is_needs_handoff(self):
        assert _derive_status({"handoff_state": "human_active", "final_answer": "x"}) == "needs_handoff"


class TestNewCsGraphInput:
    def test_has_all_required_fields(self):
        inp = new_cs_graph_input("msg", "u1", "s1", "c1", {"domain": "KNOWLEDGE"})
        assert inp["user_message"] == "msg"
        assert inp["user_id"] == "u1"
        assert inp["session_id"] == "s1"
        assert inp["conversation_id"] == "c1"
        assert inp["cs_route"] == {"domain": "KNOWLEDGE"}
        assert inp["expert_loop_count"] == 0
        assert inp["last_expert_result"] == {}
        assert inp["pending_action"] is None


class TestBuildCsGraphCompiles:
    def test_graph_compiles(self):
        with patch(
            "backend.customer_service.graph_builder.get_state_transition_service"
        ) as mock_svc:
            mock_svc.return_value.load_snapshot.return_value = {
                "conversation_status": "open",
                "handling_mode": "ai",
                "handoff_state": "ai_active",
                "confirmation_state": "not_required",
                "pending_action": None,
            }
            graph = build_cs_graph()
            assert graph is not None
