# -*- coding: utf-8 -*-
"""direct 模式全链路 e2e（router → tool_selector → skill_executor → reporter）。

用真实 build_graph() 图 + mock 各 LLM 依赖（router 三层路由 / tool_selector
FC 选择 / reporter 汇总），验证 tool_selector 接线与 resolved_params 的
端到端流动——节点级单测覆盖不到图边与 state 合并。
"""
from langchain_core.messages import AIMessage

import backend.orchestration.graph.router_node as router_mod
import backend.orchestration.graph.tool_selector as ts
from backend.orchestration.graph.builder import build_graph
from backend.orchestration.graph.events import make_initial_state
from backend.orchestration.router.types import (
    CapabilityScore, ExecutionMode, RouteDecision,
)


class _FakeRouter:
    """跳过三层路由，直接给 direct 决策（report.generate 灰区 → FC 路径）。"""

    def __init__(self, decision):
        self._decision = decision

    def route(self, query):
        return self._decision


class _FakeFC:
    """bind_tools 桩：返回带 tool_calls 的 AIMessage。"""

    def __init__(self):
        self.calls = 0

    def bind_tools(self, tools):
        outer = self

        class _Bound:
            def invoke(self, input=None, **kw):
                outer.calls += 1
                return AIMessage(content="", tool_calls=[{
                    "name": "report__generate",
                    "args": {"report_type": "daily_sales"},
                    "id": "c1",
                }])

        return _Bound()


def _run_direct_flow(monkeypatch, seen):
    decision = RouteDecision(
        execution_mode=ExecutionMode.DIRECT,
        candidates=[CapabilityScore(name="report.generate", score=0.72)],
        confidence=0.72,
    )
    monkeypatch.setattr(router_mod, "get_router", lambda: _FakeRouter(decision))
    # try_cs_prefilter 在 router_node 函数体内局部 import，patch 源模块
    import backend.orchestration.graph.cs_prefilter as cs_prefilter_mod
    monkeypatch.setattr(cs_prefilter_mod, "try_cs_prefilter", lambda q, s: None)

    fake_fc = _FakeFC()
    monkeypatch.setattr(ts, "bind_tools_for_model", lambda n, t: None)
    monkeypatch.setattr(ts, "llm", fake_fc)

    async def fake_report_node(state):
        sid = state["current_step_id"]
        seen["params"] = dict(state["plan"]["nodes"][sid]["params"])
        return {"step_results": {sid: {
            "step_id": sid, "status": "success", "output": "报告已生成",
            "capability": "report.generate", "description": "直接执行",
        }}}

    import backend.orchestration.graph.direct_executor as de
    monkeypatch.setattr(
        de.tool_registry, "get_skill_nodes",
        lambda: {"report_skill": fake_report_node})

    import backend.agents.reporter.reporter as reporter_mod

    class _ReporterLLM:
        def stream(self, msgs, **kw):
            for token in ("汇", "总", "完", "成"):
                yield AIMessage(content=token)

        def invoke(self, msgs, **kw):
            return AIMessage(content="汇总完成")

    monkeypatch.setattr(reporter_mod, "llm", _ReporterLLM())

    graph = build_graph()
    state = make_initial_state("生成上个月Amazon US的销售日报", "e2e-direct",
                               "default", [])
    state["session_id"] = "e2e-direct"

    node_order = []
    final_state = None
    for update in graph.stream(state):
        for node_name in update:
            if node_name in ("router", "tool_selector", "skill_executor", "reporter"):
                node_order.append(node_name)
        if "__end__" in update:
            final_state = update
    return node_order, fake_fc, final_state


def test_direct_flow_router_to_reporter(monkeypatch):
    """真图全链路：节点顺序 router → tool_selector → skill_executor →
    reporter，FC 填参穿透到 skill 的 plan.nodes.params"""
    seen = {}
    node_order, fake_fc, _ = _run_direct_flow(monkeypatch, seen)

    assert node_order[:4] == ["router", "tool_selector", "skill_executor", "reporter"]
    # FC 真实介入（灰区 0.72 非 fast_path）
    assert fake_fc.calls == 1
    # 端到端断言：FC 抽取的 enum 参数传到了 skill 节点
    assert seen["params"] == {"report_type": "daily_sales"}
