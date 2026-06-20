"""
graph.py 测试 — 路由与事件解析（纯逻辑，无 LLM）

覆盖:
  - route_after_planner(): Planner 后路由决策
  - _parse_event(): LangGraph stream 事件解析
"""
import pytest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from multi_agent.graph import route_after_planner, _parse_event


class TestRouteAfterPlanner:
    """Planner 后路由"""

    def test_has_plan_goes_to_supervisor(self):
        state = {
            "plan": {
                "nodes": {"1": {"step_id": "1", "capability": "search_knowledge"}},
                "edges": {},
            },
        }
        assert route_after_planner(state) == "supervisor"

    def test_empty_nodes_goes_to_reporter(self):
        state = {
            "plan": {"nodes": {}, "edges": {}},
        }
        assert route_after_planner(state) == "reporter"

    def test_missing_plan_goes_to_reporter(self):
        state = {"plan": {}}
        result = route_after_planner(state)
        # nodes 不存在 → .get("nodes", {}) 返回 {} → reporter
        assert result == "reporter"

    def test_no_plan_field(self):
        state = {}
        result = route_after_planner(state)
        assert result == "reporter"


class TestParseEvent:
    """LangGraph stream 事件解析"""

    def test_known_node(self):
        event = {"planner": {"plan": {"nodes": {}, "edges": {}}}}
        node, output = _parse_event(event)
        assert node == "planner"
        assert output["plan"] == {"nodes": {}, "edges": {}}

    def test_worker_node(self):
        event = {"sql_worker": {"step_results": {"1": {"status": "success"}}}}
        node, output = _parse_event(event)
        assert node == "sql_worker"

    def test_unknown_node_ignored(self):
        """非已知节点的事件被忽略"""
        event = {"__start__": {}}
        node, output = _parse_event(event)
        assert node is None

    def test_not_a_dict(self):
        node, output = _parse_event("not a dict")
        assert node is None

    def test_empty_dict(self):
        node, output = _parse_event({})
        assert node is None

    def test_first_known_node_returned(self):
        """多个 key 时返回第一个已知节点"""
        event = {"planner": {"plan": {}}, "supervisor": {"_ready_dispatch": []}}
        node, _ = _parse_event(event)
        assert node == "planner"  # planner 先于 supervisor

    def test_reporter_event(self):
        event = {"reporter": {"final_answer": "## 报告内容"}}
        node, output = _parse_event(event)
        assert node == "reporter"
        assert output["final_answer"] == "## 报告内容"

    def test_supervisor_event(self):
        event = {"supervisor": {"_all_steps_done": True, "step_results": {}}}
        node, output = _parse_event(event)
        assert node == "supervisor"
