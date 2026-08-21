# -*- coding: utf-8 -*-
"""direct_executor 回归测试。

覆盖:
  - f11: 无 candidates / skill 不存在时补 failed step_results
  - f12: _extract_capability_name 走 CAPABILITY_MAP（business.analyze →
         business_analysis_skill，而非字符串拼接的 business_skill）
  - f13: business.analyze direct 单步执行缺 previous_outputs 时，
         自动补前置 sql.query 步骤形成两段微编排
"""
from backend.orchestration.graph import direct_executor
from backend.orchestration.graph.direct_executor import (
    _extract_capability_name,
    skill_executor_node,
)


def _mk_candidates(name, score=0.9):
    return [{"name": name, "score": score}]


def _state(question="测试问题", candidates=None, **extra):
    st = {
        "question": question,
        "route_decision": {"candidates": candidates or []},
    }
    st.update(extra)
    return st


class TestExtractCapabilityName:
    def test_business_analyze_uses_registry(self):
        """fix f12：注册表派生节点名优先（business.analyze → business_analysis_skill）"""
        # skills registry 已模块级注册，CAPABILITY_MAP 含 business.analyze
        assert _extract_capability_name("business.analyze") == "business_analysis_skill"

    def test_registered_cap_maps_to_skill_node(self):
        assert _extract_capability_name("sql.query") == "sql_skill"
        assert _extract_capability_name("rag.search") == "rag_skill"

    def test_unknown_cap_falls_back_to_prefix_concat(self):
        assert _extract_capability_name("unknown.cap") == "unknown_skill"
        assert _extract_capability_name("plainname") == "plainname"


class TestSkillExecutorFailureBranches:
    def test_no_candidates_returns_failed_step(self, monkeypatch):
        """fix f11：无 candidates 时补 failed step_results 供 reporter/trace 使用"""
        out = skill_executor_node(_state(candidates=[]))
        assert out["executor_error"] == "no_candidates"
        sr = out["step_results"]
        assert sr["direct_1"]["status"] == "failed"

    def test_skill_not_found_returns_failed_step(self, monkeypatch):
        monkeypatch.setattr(
            direct_executor.tool_registry, "get_skill_nodes", lambda: {})
        out = skill_executor_node(_state(candidates=_mk_candidates("rag.search")))
        assert out["executor_error"].startswith("skill_not_found")
        assert out["step_results"]["direct_1"]["status"] == "failed"


class TestDirectExecution:
    def _patch_nodes(self, monkeypatch, nodes):
        monkeypatch.setattr(
            direct_executor.tool_registry, "get_skill_nodes", lambda: dict(nodes))

    def test_single_step_success(self, monkeypatch):
        async def fake_sql(state):
            sid = state["current_step_id"]
            return {"step_results": {sid: {
                "status": "success", "output": {"rows": [{"x": 1}]}}}}

        self._patch_nodes(monkeypatch, {"sql_skill": fake_sql})
        out = skill_executor_node(_state(candidates=_mk_candidates("sql.query")))
        assert out["executor_mode"] == "direct"
        assert out["step_results"]["direct_1"]["status"] == "success"
        assert out["final_answer"] == {"rows": [{"x": 1}]}

    def test_business_analyze_auto_runs_predecessor(self, monkeypatch):
        """fix f13：business.analyze 无前置输出 → 先跑 sql.query（direct_0），
        其 output 注入 previous_outputs 后再跑 business.analyze（direct_1）"""
        calls = []

        async def fake_sql(state):
            calls.append(("sql", state.get("previous_outputs")))
            sid = state["current_step_id"]
            return {"step_results": {sid: {
                "status": "success",
                "output": {"sql": "SELECT 1", "tables": ["t"], "columns": ["c"],
                           "rows": [{"c": 1}], "row_count": 1,
                           "execution_time": 0.1}}}}

        async def fake_analyze(state):
            calls.append(("analyze", dict(state.get("previous_outputs") or {})))
            sid = state["current_step_id"]
            return {"step_results": {sid: {
                "status": "success", "output": {"summary": "洞察"}}}}

        self._patch_nodes(monkeypatch, {
            "sql_skill": fake_sql,
            "business_analysis_skill": fake_analyze,
        })
        out = skill_executor_node(
            _state(question="分析库存周转", candidates=_mk_candidates("business.analyze")))

        assert out["executor_mode"] == "direct"
        sr = out["step_results"]
        # 两段微编排：direct_0 前置 + direct_1 本体
        assert sr["direct_0"]["status"] == "success"
        assert sr["direct_1"]["status"] == "success"
        # analyze 收到的 previous_outputs 来自前置步骤 output
        kind, prev = calls[-1]
        assert kind == "analyze"
        assert prev["direct_0"]["row_count"] == 1

    def test_business_analyze_with_existing_predecessor_skips_prefetch(self, monkeypatch):
        """已有 previous_outputs（plan 模式传递场景）时不重复补前置步骤"""
        async def fake_analyze(state):
            sid = state["current_step_id"]
            return {"step_results": {sid: {"status": "success", "output": {"summary": "x"}}}}

        self._patch_nodes(monkeypatch, {"business_analysis_skill": fake_analyze})
        out = skill_executor_node(_state(
            candidates=_mk_candidates("business.analyze"),
            previous_outputs={"plan_1": {"rows": []}}))
        assert "direct_0" not in out["step_results"]
        assert out["step_results"]["direct_1"]["status"] == "success"
