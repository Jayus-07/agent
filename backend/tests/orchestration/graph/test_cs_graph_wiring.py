"""tests/orchestration/graph/test_cs_graph_wiring.py — 域图注册表布线测试

验证:
  1. route_selector: customer_service 模式 → cs_graph_node（通过 registry）
  2. Main Graph 编译成功（含 cs_graph_node，不含旧节点）
  3. 非 CS 路由不受影响
"""
from __future__ import annotations

import backend.domains  # noqa: F401 — 触发域图自注册
from backend.orchestration.graph.router_node import route_selector


class TestRouteSelectorCSGraph:
    def test_cs_mode_routes_to_cs_graph_node(self):
        state = {
            "route_mode": "customer_service",
            "cs_context": {"cs_target": "cs_knowledge"},
        }
        assert route_selector(state) == "cs_graph_node"

    def test_cs_mode_ignores_cs_target(self):
        """不管 cs_target 是什么，都走 cs_graph_node"""
        for target in ["cs_knowledge", "cs_business_query", "cs_complaint", "cs_handoff"]:
            state = {
                "route_mode": "customer_service",
                "cs_context": {"cs_target": target},
            }
            assert route_selector(state) == "cs_graph_node"

    def test_cs_mode_empty_context(self):
        state = {
            "route_mode": "customer_service",
            "cs_context": {},
        }
        assert route_selector(state) == "cs_graph_node"


class TestRouteSelectorNonCSUnaffected:
    def test_plan_mode(self):
        assert route_selector({"route_mode": "plan"}) == "planner"

    def test_direct_mode(self):
        assert route_selector({"route_mode": "direct"}) == "skill_executor"

    def test_workflow_mode(self):
        assert route_selector({"route_mode": "workflow"}) == "workflow_executor"

    def test_default_mode_is_planner(self):
        assert route_selector({}) == "planner"


class TestMainGraphCompilation:
    def test_main_graph_compiles_with_cs_graph_node(self):
        from backend.orchestration.graph.builder import build_graph
        graph = build_graph()
        assert graph is not None

    def test_cs_graph_node_in_node_labels(self):
        from backend.orchestration.graph.builder import _NODE_LABELS
        assert "cs_graph_node" in _NODE_LABELS
        assert _NODE_LABELS["cs_graph_node"] == "客服图执行"

    def test_old_cs_nodes_not_in_labels(self):
        from backend.orchestration.graph.builder import _NODE_LABELS
        old_nodes = [
            "cs_knowledge", "cs_business_query", "cs_business_action",
            "cs_pending", "cs_complaint", "cs_handoff", "cs_handoff_intercept",
        ]
        for node in old_nodes:
            assert node not in _NODE_LABELS, f"Old node {node} should be removed"
