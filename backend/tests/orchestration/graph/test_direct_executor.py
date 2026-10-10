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
    _coerce_final_answer,
    skill_executor_node,
    workflow_executor_node,
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
    def test_selection_blocked_does_not_execute_first_candidate(self, monkeypatch):
        called = []

        async def should_not_run(_state):
            called.append("executed")
            return {"step_results": {"direct_1": {"status": "success"}}}

        monkeypatch.setattr(
            direct_executor.tool_registry, "get_skill_nodes",
            lambda: {"report_skill": should_not_run, "web_search_skill": should_not_run},
        )
        out = skill_executor_node(_state(
            candidates=_mk_candidates("report.generate"),
            selection_blocked=True,
        ))
        assert out["executor_error"] == "tool_selection_requires_clarification"
        assert out["step_results"]["direct_1"]["status"] == "failed"
        assert called == []

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
        assert isinstance(out["final_answer"], str)
        assert "| x |" in out["final_answer"]
        assert "| 1 |" in out["final_answer"]
        # step_results 保留结构化原貌，供 reporter/trace 使用
        assert out["step_results"]["direct_1"]["output"] == {"rows": [{"x": 1}]}

    def test_coerce_business_insight_to_readable_markdown(self):
        answer = _coerce_final_answer({
            "capability": "business.analyze",
            "description": "库存分析",
            "status": "success",
            "output": {
                "summary": "库存周转较慢",
                "risks": ["滞销库存增加"],
                "suggestions": ["优先处理滞销商品"],
                "confidence": 0.8,
            },
        })

        assert "库存周转较慢" in answer
        assert "滞销库存增加" in answer
        assert "优先处理滞销商品" in answer
        assert not answer.lstrip().startswith("{")

    def test_coerce_sql_zero_rows_as_successful_empty_query(self):
        answer = _coerce_final_answer({
            "capability": "sql.query",
            "description": "订单查询",
            "status": "success",
            "is_empty": True,
            "output": {"columns": ["order_id"], "rows": []},
        })

        assert "查询成功" in answer
        assert "0 行" in answer
        assert "未能找到与" not in answer

    def test_coerce_unknown_and_compacted_objects_without_repr(self):
        unknown = _coerce_final_answer({
            "capability": "unknown.capability",
            "description": "内部结果",
            "status": "success",
            "output": {"opaque_internal_key": "secret-value"},
        })
        preview = _coerce_final_answer({
            "capability": "map.lookup",
            "description": "地图查询",
            "status": "success",
            "output": {
                "context_compacted": True,
                "type": "tool_result_preview",
                "preview": "{\"context_compacted\": true, \"token\": \"secret\"}",
            },
        })

        assert "无法可靠展示" in unknown
        assert "opaque_internal_key" not in unknown
        assert "secret-value" not in unknown
        assert "无法可靠展示" in preview
        assert "context_compacted" not in preview
        assert "secret" not in preview


def test_workflow_executor_renders_actual_business_result_without_run_metadata(monkeypatch):
    from types import SimpleNamespace
    import backend.orchestration.workflow.scheduler as scheduler_mod

    context = SimpleNamespace(
        status="success",
        error="",
        outputs={"analysis": {
            "summary": "库存周转偏慢",
            "risks": ["滞销库存增加"],
            "suggestions": ["优先处理滞销商品"],
        }},
        step_failures=[],
        run_id="synthetic-private-run-id",
    )

    class _Scheduler:
        async def run_now(self, workflow_name, inputs=None):
            return context

    monkeypatch.setattr(scheduler_mod, "get_workflow_scheduler", lambda: _Scheduler())
    out = workflow_executor_node({
        "question": "生成库存分析",
        "route_decision": {"workflow_name": "inventory_summary"},
    })

    assert "库存周转偏慢" in out["final_answer"]
    assert "滞销库存增加" in out["final_answer"]
    assert "synthetic-private-run-id" not in out["final_answer"]
    assert "inventory_summary" not in out["final_answer"]
    assert not out["final_answer"].lstrip().startswith("{")

    def test_failed_step_empty_final_answer(self, monkeypatch):
        """失败步骤 final_answer 必须为空串：曾返回 str(None)="None"，
        占住 truthy final_answer 后 reporter 的降级文案被 runner 忽略，
        用户看到字面量 "None" 且被记忆落库"""
        async def failing_sql(state):
            sid = state["current_step_id"]
            return {"step_results": {sid: {
                "status": "failed", "output": None,
                "error": "no such table: orders", "error_type": "not_found"}}}

        self._patch_nodes(monkeypatch, {"sql_skill": failing_sql})
        out = skill_executor_node(_state(candidates=_mk_candidates("sql.query")))
        assert out["final_answer"] == ""
        sr = out["step_results"]["direct_1"]
        assert sr["status"] == "failed"
        assert sr["error_type"] == "not_found"

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

    def test_resolved_params_flow_into_skill(self, monkeypatch):
        """tool_selector FC 填参结果必须进入 plan.nodes[direct_1].params"""
        seen = {}

        async def fake_report(state):
            sid = state["current_step_id"]
            seen["params"] = state["plan"]["nodes"][sid]["params"]
            return {"step_results": {sid: {
                "status": "success", "output": "报告已生成"}}}

        self._patch_nodes(monkeypatch, {"report_skill": fake_report})
        out = skill_executor_node(_state(
            candidates=_mk_candidates("report.generate"),
            resolved_params={"report_type": "daily_sales",
                             "filters": {"channel": "Amazon US"}},
        ))
        assert seen["params"] == {"report_type": "daily_sales",
                                  "filters": {"channel": "Amazon US"}}
        assert out["step_results"]["direct_1"]["status"] == "success"

    def test_empty_resolved_params_falls_back_to_question(self, monkeypatch):
        """resolved_params 为空 dict（ falsy）时回退 question 透传（旧语义）"""
        seen = {}

        async def fake_rag(state):
            sid = state["current_step_id"]
            seen["params"] = state["plan"]["nodes"][sid]["params"]
            return {"step_results": {sid: {
                "status": "success", "output": "ok"}}}

        self._patch_nodes(monkeypatch, {"rag_skill": fake_rag})
        skill_executor_node(_state(
            question="退款政策是什么",
            candidates=_mk_candidates("rag.search"),
            resolved_params={},
        ))
        assert seen["params"] == {"question": "退款政策是什么"}

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


def test_business_analyze_does_not_receive_question_fallback():
    """有 auto 前置依赖的 direct 能力不接收通用 question 参数。"""
    state = _state(question="分析库存周转", candidates=_mk_candidates("business.analyze"))

    assert direct_executor._resolved_params(state, "business.analyze") == {}
    assert direct_executor._resolved_params(state, "sql.query") == {
        "question": "分析库存周转"
    }
