"""test_cs_graph_node_adapter.py — cs_graph_node 适配器测试

Phase 0.12: 验证 Main State ↔ CS Graph 适配逻辑
- 输入映射 (OrchestratorState → CS Graph input)
- 输出映射 (CSGraphResult → Main State)
- cs_context 合并策略
- 异常安全网
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.orchestration.graph.cs_graph_node import (
    cs_graph_node,
    _build_main_state_update,
    _fallback_update,
    _FALLBACK_ANSWER,
)


def _make_main_state(**overrides) -> dict:
    base = {
        "question": "如何退货？",
        "cs_context": {
            "authenticated_user_id": "u1",
            "session_id": "s1",
            "conversation_id": "c1",
            "cs_target": "cs_knowledge",
            "cs_route": {"domain": "KNOWLEDGE"},
            "handoff_state": "ai_active",
            "confirmation_state": "not_required",
        },
    }
    base.update(overrides)
    return base


class TestCsGraphNodeMapping:
    def test_build_main_state_update_success(self):
        state = _make_main_state()
        result = {
            "final_answer": "退货流程如下...",
            "conversation_id": "c1",
            "handoff_state": "ai_active",
            "confirmation_state": "not_required",
            "action_result": None,
            "audit_entries": [{"action": "knowledge_query"}],
        }
        update = _build_main_state_update(state, result)

        assert update["final_answer"] == "退货流程如下..."
        assert update["cs_context"]["authenticated_user_id"] == "u1"
        assert update["cs_context"]["session_id"] == "s1"
        assert update["cs_context"]["cs_target"] == "cs_knowledge"
        assert update["cs_context"]["conversation_id"] == "c1"
        assert update["cs_audit_entries"] == [{"action": "knowledge_query"}]

    def test_cs_context_preserves_original_fields(self):
        state = _make_main_state()
        result = {
            "final_answer": "回答",
            "conversation_id": "c-new",
            "handoff_state": "handoff_requested",
            "confirmation_state": "pending",
        }
        update = _build_main_state_update(state, result)

        ctx = update["cs_context"]
        assert ctx["authenticated_user_id"] == "u1"
        assert ctx["session_id"] == "s1"
        assert ctx["cs_target"] == "cs_knowledge"
        assert ctx["conversation_id"] == "c-new"
        assert ctx["handoff_state"] == "handoff_requested"
        assert ctx["confirmation_state"] == "pending"


class TestFallbackUpdate:
    def test_fallback_returns_safe_answer(self):
        state = _make_main_state()
        update = _fallback_update(state)

        assert update["final_answer"] == _FALLBACK_ANSWER
        assert update["cs_context"]["authenticated_user_id"] == "u1"
        assert update["cs_context"]["session_id"] == "s1"
        assert update["cs_action_result"] == {}
        assert update["cs_audit_entries"] == []


class TestCsGraphNodeSafetyNet:
    def test_exception_returns_fallback(self):
        state = _make_main_state()
        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = RuntimeError("CS Graph crashed")

        with patch(
            "backend.orchestration.graph.cs_graph_node.get_cs_graph",
            return_value=mock_graph,
        ):
            result = cs_graph_node(state)

        assert result["final_answer"] == _FALLBACK_ANSWER
        assert result["cs_context"]["authenticated_user_id"] == "u1"

    def test_success_path_invokes_graph(self):
        state = _make_main_state()
        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {
            "final_answer": "测试回答",
            "conversation_id": "c1",
            "handoff_state": "ai_active",
            "confirmation_state": "not_required",
            "cs_action_result": {},
            "cs_audit_entries": [],
            "last_expert_result": {},
        }

        with patch(
            "backend.orchestration.graph.cs_graph_node.get_cs_graph",
            return_value=mock_graph,
        ):
            result = cs_graph_node(state)

        assert result["final_answer"] == "测试回答"
        mock_graph.invoke.assert_called_once()
        invoke_arg = mock_graph.invoke.call_args[0][0]
        assert invoke_arg["user_message"] == "如何退货？"
        assert invoke_arg["user_id"] == "u1"
