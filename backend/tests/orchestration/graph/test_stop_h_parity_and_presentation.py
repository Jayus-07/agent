"""test_stop_h_parity_and_presentation.py — STOP H：跨路径 parity + 失败呈现

2026-10-07 全项目 Tool Failure Semantics 收口（任务 §十三~十八/§二十六）：
- 同一 Tool 失败在 direct / workflow（/planner）三条路径的 tool_status 同源；
- RuntimeResult 归一：degraded/partial 映射 partial，不误成 success/error；
- Reporter partial 场景：成功结果保留 + 失败交代，workflow partial 不再
  误发「抱歉+追问卡」；
- SSE：degraded 是 info 不是 error；todo partial 是终态不再永久 in_progress。
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch as mp

import pytest

from backend.orchestration.graph.direct_executor import skill_executor_node
from backend.orchestration.graph.events import _TODO_STATUS_MAP, make_todo_event
from backend.orchestration.workflow.skill_adapter import SkillStepFailure, call_skill


# ─────────────────────────────────────────────────────────────
# 跨路径 parity
# ─────────────────────────────────────────────────────────────

_GOVERNED_FAILED_SR = {
    "status": "failed",
    "output": None,
    "error": "服务暂时不可用",
    "error_type": "network",
    "tool_status": "unavailable",
    "criticality": "important",
    "error_code": "connect_error",
}


def test_cross_path_tool_status_parity():
    """同一治理失败形态经三条路径，tool_status/criticality/error_code 全同源：
    - workflow：SkillAdapter 上抛的 SkillStepFailure.step_result；
    - direct：_run_skill_step 透传后的 step dict（此前 tool_status 在此丢失）；
    - planner：BaseSkill 直写的 step_results（同 _GOVERNED_FAILED_SR 形态，
      与 workflow 同源——workflow 路径的 sr 即 BaseSkill.execute 产物）。"""

    # workflow 路径
    class _FakeSkill:
        name = "rag"

        async def execute(self, state, step_capability="", **kwargs):
            return {"step_results": {"rag": dict(_GOVERNED_FAILED_SR)}}

    async def run_workflow_path():
        with mp("backend.skills.rag.skill.RAGSkill", _FakeSkill):
            with pytest.raises(SkillStepFailure) as ei:
                await call_skill("rag", "rag.search", {"question": "x"})
        return ei.value.step_result

    wf_sr = asyncio.run(run_workflow_path())

    # direct 路径（fake skill 节点返回同一 sr 形态）
    async def fake_skill_node(state: dict) -> dict:
        return {"step_results": {state["current_step_id"]: dict(_GOVERNED_FAILED_SR)}}

    state = {
        "question": "查销售",
        "route_decision": {"candidates": [{"name": "sql.query", "score": 0.9}]},
    }
    with mp("backend.orchestration.graph.direct_executor.tool_registry") as reg:
        reg.get_skill_nodes.return_value = {"sql_skill": fake_skill_node}
        reg.get_node.return_value = "sql_skill"
        direct_out = skill_executor_node(state)
    direct_step = direct_out["step_results"]["direct_1"]

    for key in ("tool_status", "criticality", "error_code", "error_type"):
        assert wf_sr[key] == direct_step[key] == _GOVERNED_FAILED_SR[key], (
            f"parity 断裂: {key} workflow={wf_sr.get(key)!r} "
            f"direct={direct_step.get(key)!r}"
        )
    assert direct_step["status"] == "failed"
    assert direct_out["final_answer"] == ""


# ─────────────────────────────────────────────────────────────
# RuntimeResult 归一
# ─────────────────────────────────────────────────────────────

class TestRuntimeResultStatusMapping:

    def test_runtime_result_degraded_maps_partial(self):
        from backend.orchestration.runtime_result_adapter import build_runtime_result

        # 域图显式 partial（降级产物）→ partial，不误成 success
        r = build_runtime_result(
            {"final_answer": "部分数据", "status": "partial"},
            runtime_id="test.domain",
        )
        assert r.status == "partial"

        # 未知 status 归 partial（保守），缺省才是 success
        r2 = build_runtime_result({"final_answer": "x", "status": "weird"},
                                  runtime_id="test.domain")
        assert r2.status == "partial"
        r3 = build_runtime_result({"final_answer": "x"}, runtime_id="test.domain")
        assert r3.status == "success"

        # failed/error → error
        r4 = build_runtime_result({"final_answer": "x", "status": "failed"},
                                  runtime_id="test.domain")
        assert r4.status == "error"


# ─────────────────────────────────────────────────────────────
# Reporter partial 呈现
# ─────────────────────────────────────────────────────────────

class TestReporterPartialPresentation:

    def test_reporter_partial_result_preserves_successful_output(self):
        """SQL 成功 + RAG 失败：SQL 表格保留 + 失败显式交代（不是只剩抱歉）"""
        import backend.orchestration.graph  # noqa: F401（规避循环导入，同 test_reporter_degraded）
        from backend.agents.reporter.reporter import generate_final_answer

        step_results = {
            "s1": {
                "step_id": "s1", "capability": "sql.query", "description": "数据库查询",
                "status": "success",
                "output": {"columns": ["product"], "rows": [{"product": "A", "qty": 5}]},
            },
            "s2": {
                "step_id": "s2", "capability": "rag.search", "description": "知识库检索",
                "status": "failed", "output": None,
                "error": "服务暂时不可用", "error_type": "network",
                "tool_status": "unavailable",
            },
        }
        answer = generate_final_answer("卖得怎么样？", step_results, context_filter=False)
        assert "product" in answer or "A" in answer, "成功步骤的 SQL 结果必须保留"
        assert "抱歉" not in answer.split("\n")[0], "有可用结果时不得整答降级为抱歉"
        assert "服务暂时不可用" in answer or "未获得数据" in answer, (
            "失败步骤必须显式交代"
        )

    def test_workflow_partial_step_no_false_refusal_clarify(self):
        """workflow partial 步骤不再触发「## 抱歉 + 追问卡」误发"""
        import backend.orchestration.graph  # noqa: F401
        from backend.agents.reporter.reporter import reporter_node

        state = {
            "question": "生成今天的经营日报",
            "step_results": {
                "workflow_daily_report": {
                    "step_id": "workflow_daily_report",
                    "capability": "workflow",
                    "description": "工作流 daily_report 执行",
                    "status": "partial",
                    "output": "## 工作流 daily_report 执行结果\n\n> ⚠️ 以下环节数据本次不可用：rag_query_template",
                },
            },
        }
        result = reporter_node(state)
        assert "_clarify" not in result and "clarification_request" not in result, (
            "partial workflow 不得误发追问卡（与已产出结果自相矛盾）"
        )
        answer = result.get("final_answer", "")
        assert not answer.startswith("## 抱歉"), "partial 不得渲染成整体拒答"


# ─────────────────────────────────────────────────────────────
# SSE / todo
# ─────────────────────────────────────────────────────────────

def test_todo_partial_is_terminal():
    assert _TODO_STATUS_MAP["partial"] == "completed"
    plan_nodes = {"s1": {"description": "查询"}}
    evt = make_todo_event(plan_nodes, {"s1": {"status": "partial"}})
    assert evt["data"]["items"][0]["status"] == "completed"


def _skill_events(step_results: dict, node_name: str = "rag_skill"):
    from backend.orchestration.graph.events import _build_skill_events
    return list(_build_skill_events(
        node_name, {"step_results": step_results},
        lambda sr, include_output=False: {"step_id": sr.get("step_id")},
    ))


def test_skill_log_degraded_is_info_not_error():
    """degraded 成功步骤：info 级 + 降级文案（degraded ≠ failed）"""
    sr = {"s1": {
        "step_id": "s1", "capability": "rag.search", "description": "知识库检索",
        "status": "success", "output": "降级后的可用结果",
        "degraded": True, "tool_status": "degraded",
    }}
    events = _skill_events(sr)
    assert events, "降级成功步骤必须产生 log 事件"
    data = events[0]["data"]
    assert data["level"] == "info"
    assert "降级" in data["message"]


def test_skill_log_failed_still_error():
    sr = {"s1": {
        "step_id": "s1", "capability": "sql.query", "description": "数据库查询",
        "status": "failed", "output": None,
        "error": "服务暂时不可用", "tool_status": "unavailable",
    }}
    events = _skill_events(sr, node_name="sql_skill")
    assert events and events[0]["data"]["level"] == "error"
