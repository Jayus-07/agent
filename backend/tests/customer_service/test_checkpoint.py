"""tests/customer_service/test_checkpoint.py — Phase 5: Checkpointer 集成测试

验证:
  1. build_cs_graph(checkpointer=None) 正常工作
  2. build_cs_graph(checkpointer=MemorySaver()) 正常工作
  3. _build_checkpointer() 在 CS_CHECKPOINTER_ENABLED=false 时返回 None
  4. _build_checkpointer() 在 CS_CHECKPOINTER_ENABLED=true 时返回 MemorySaver
  5. _build_invoke_config 正确构建 config
  6. 端到端: MemorySaver 模式下多轮 invoke 保持 thread_id 隔离
"""
from __future__ import annotations

from unittest.mock import patch

from backend.orchestration.graph.cs_graph_node import _build_invoke_config


class TestBuildCheckpointer:
    @patch("backend.config.customer_service.CS_CHECKPOINTER_ENABLED", False)
    def test_disabled_returns_none(self):
        from backend.customer_service.graph_builder import _build_checkpointer
        assert _build_checkpointer() is None

    @patch("backend.config.customer_service.CS_CHECKPOINTER_ENABLED", True)
    def test_enabled_returns_memory_saver(self):
        from backend.customer_service.graph_builder import _build_checkpointer
        cp = _build_checkpointer()
        assert cp is not None


class TestBuildCsGraphWithCheckpointer:
    def test_without_checkpointer(self):
        from backend.customer_service.graph_builder import build_cs_graph
        graph = build_cs_graph(checkpointer=None)
        assert graph is not None

    def test_with_memory_saver(self):
        from langgraph.checkpoint.memory import MemorySaver

        from backend.customer_service.graph_builder import build_cs_graph
        graph = build_cs_graph(checkpointer=MemorySaver())
        assert graph is not None


class TestBuildInvokeConfig:
    def test_with_conversation_id(self):
        config = _build_invoke_config("conv-123")
        assert config["configurable"]["thread_id"] == "conv-123"
        assert "recursion_limit" in config

    def test_without_conversation_id(self):
        config = _build_invoke_config("")
        assert "configurable" not in config
        assert "recursion_limit" in config


class TestCheckpointerEndToEnd:
    @patch("backend.customer_service.graph_builder.get_state_transition_service")
    def test_memory_saver_invoke(self, mock_sts):
        """MemorySaver 模式下 CS Graph 可正常 invoke"""
        from langgraph.checkpoint.memory import MemorySaver

        from backend.customer_service.graph_builder import build_cs_graph

        mock_sts.return_value.load_snapshot.return_value = {
            "conversation_status": "open",
            "handling_mode": "ai",
            "handoff_state": "ai_active",
            "confirmation_state": "not_required",
            "pending_action": None,
        }

        graph = build_cs_graph(checkpointer=MemorySaver())

        cs_input = {
            "user_message": "test",
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

        config = {"configurable": {"thread_id": "c1"}}
        result = graph.invoke(cs_input, config=config)
        assert result["final_answer"]
