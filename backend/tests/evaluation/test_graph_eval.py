# -*- coding: utf-8 -*-
"""Graph E2E 评测模块测试。

覆盖：
  1. 数据集可加载、用例结构合法（module=e2e、tier、id 唯一、workflow/capabilities 声明）
  2. 离线 runner 全部 pass/skip（对抗用例不进图）
  3. 在线判分逻辑（_eval_graph_case / _judge_final_state）用合成 final_state 单测，
     不依赖真实 LLM / 图执行
"""
import pytest

from backend.evaluation.dataset import load_dataset
from backend.evaluation.models import TestCase
from backend.evaluation.runners.e2e import (
    _judge_final_state,
    _run_e2e,
    _eval_graph_case,
)


@pytest.fixture(scope="module")
def e2e_cases():
    return load_dataset("e2e")


class TestDataset:
    def test_loads_and_counts(self, e2e_cases):
        assert len(e2e_cases) == 25

    def test_ids_unique(self, e2e_cases):
        ids = [c.id for c in e2e_cases]
        assert len(ids) == len(set(ids))
        assert all(i.startswith(("G-", "F-")) for i in ids)

    def test_module_field(self, e2e_cases):
        assert all(c.module == "e2e" for c in e2e_cases)

    def test_tiers_present(self, e2e_cases):
        tiers = {c.metadata.get("tier") for c in e2e_cases}
        assert {"smoke", "core", "hard"} <= tiers

    def test_workflow_cases_have_name(self, e2e_cases):
        for c in e2e_cases:
            if c.expected.get("route") == "workflow":
                assert c.expected.get("workflow_name"), f"{c.id} 缺少 workflow_name"

    def test_graph_cases_have_capabilities(self, e2e_cases):
        for c in e2e_cases:
            exp = c.expected
            if exp.get("should_block") or exp.get("fault") \
                    or exp.get("route") == "workflow":
                continue
            assert exp.get("capabilities"), f"{c.id} 缺少 capabilities"

    def test_guard_cases_declared(self, e2e_cases):
        guards = [c for c in e2e_cases if c.expected.get("should_block")]
        assert len(guards) == 2
        assert all(c.expected.get("final_state") == "BLOCKED" for c in guards)

    def test_fault_cases_declared(self, e2e_cases):
        """12 个故障注入用例齐全，覆盖 Skill 边界的每类语义"""
        faults = [c for c in e2e_cases if c.expected.get("fault")]
        assert len(faults) == 12
        assert {c.expected["fault"] for c in faults} == {
            "unretryable_error", "retryable_error", "timeout",
            "output_dict_normalized", "structured_str_parsed",
            "param_missing_required", "param_enum_violation",
            "approval_pending", "executor_no_candidates",
            "executor_skill_not_found", "executor_step_failed",
            "sqlresult_rendered",
        }


class TestOfflineRunner:
    def test_offline_all_pass(self, e2e_cases):
        """离线评测门禁：健全性校验 + Guard 拦截 + 故障注入（纯规则，离线确定可跑）。"""
        results = _run_e2e(e2e_cases, live=False)
        for r in results:
            assert r.status == "pass", f"{r.case_id}: {r.error_msg}"
        assert len(results) == 25

    def test_offline_guard_cases_intercepted(self, e2e_cases):
        guards = [c for c in e2e_cases if c.expected.get("should_block")]
        results = _run_e2e(guards, live=False)
        assert all(r.status == "pass" for r in results)
        assert all(r.metrics.get("guard_intercepted") == 1.0 for r in results)

    def test_offline_fault_cases_semantics(self, e2e_cases):
        """故障注入：断言每条用例的关键 metrics（重试次数/错误分类）"""
        faults = [c for c in e2e_cases if c.expected.get("fault")]
        results = {r.case_id: r for r in _run_e2e(faults, live=False)}
        assert all(r.status == "pass" for r in results.values())
        # 不可重试错误只调 1 次；可重试错误耗尽 3 次
        assert results["F-001"].metrics["tool_calls"] == 1
        assert results["F-002"].metrics["tool_calls"] == 3
        # 参数校验失败 Tool 零调用
        assert results["F-006"].metrics["tool_calls"] == 0


def _make_case(exp: dict) -> TestCase:
    return TestCase(id="T-001", question="测试问题", module="e2e", expected=exp)


def _final(route_mode: str = "plan", caps: list[str] | None = None,
           step_status: str = "success", answer: str = "## 结果\nOK",
           executor_workflow: str | None = None) -> dict:
    caps = caps or []
    return {
        "route_mode": route_mode,
        "plan": {"nodes": {str(i + 1): {"capability": c} for i, c in enumerate(caps)},
                 "edges": {}},
        "step_results": {
            str(i + 1): {"status": step_status} for i in range(len(caps))
        },
        "final_answer": answer,
        "executor_workflow": executor_workflow,
    }


class TestJudgeFinalState:
    def test_success(self):
        assert _judge_final_state(_final(answer="有答案")) == "SUCCESS"

    def test_empty_answer_is_failed(self):
        assert _judge_final_state(_final(answer="")) == "FAILED"

    def test_failed_step_is_failed(self):
        assert _judge_final_state(
            _final("plan", ["sql.query"], step_status="failed")) == "FAILED"

    def test_executor_error_is_failed(self):
        state = _final()
        state["executor_error"] = "no_candidates"
        assert _judge_final_state(state) == "FAILED"


class TestEvalGraphCase:
    def test_happy_path_pass(self):
        case = _make_case({"route": "plan",
                           "capabilities": ["sql.query", "report.generate"],
                           "final_state": "SUCCESS"})
        final = _final("plan", ["sql.query", "report.generate", "rag.search"])
        r = _eval_graph_case(case, case.expected, final)
        assert r.status == "pass"
        assert r.metrics["route_correct"] == 1.0
        # Planner 多拆了 rag.search 步骤，覆盖率仍为 1.0（多不扣分）
        assert r.metrics["capability_coverage"] == 1.0
        assert r.error_msg is None

    def test_wrong_route_fails(self):
        case = _make_case({"route": "direct", "capabilities": ["sql.query"]})
        final = _final("plan", ["sql.query"])
        r = _eval_graph_case(case, case.expected, final)
        assert r.status == "fail"
        assert r.metrics["route_correct"] == 0.0
        assert "路由期望 direct，实际 plan" in r.error_msg

    def test_missing_capability_fails(self):
        case = _make_case({"route": "plan",
                           "capabilities": ["sql.query", "email.send"]})
        final = _final("plan", ["sql.query"])
        r = _eval_graph_case(case, case.expected, final)
        assert r.status == "fail"
        assert r.metrics["capability_coverage"] == 0.5
        assert "email.send" in r.error_msg

    def test_workflow_match_and_mismatch(self):
        exp = {"route": "workflow", "workflow_name": "daily_report"}
        ok = _eval_graph_case(_make_case(exp), exp,
                              _final("workflow", executor_workflow="daily_report"))
        assert ok.status == "pass"
        assert ok.metrics["workflow_correct"] == 1.0

        bad = _eval_graph_case(_make_case(exp), exp,
                               _final("workflow", executor_workflow="inventory_alert"))
        assert bad.status == "fail"
        assert bad.metrics["workflow_correct"] == 0.0

    def test_workflow_expected_but_routed_plan_fails(self):
        exp = {"route": "workflow", "workflow_name": "daily_report"}
        r = _eval_graph_case(_make_case(exp), exp, _final("plan", []))
        assert r.status == "fail"
        # workflow 用例走错路：路由与 workflow 命中双失分
        assert r.metrics["route_correct"] == 0.0
        assert r.metrics["workflow_correct"] == 0.0

    def test_final_state_mismatch_fails(self):
        case = _make_case({"route": "plan", "capabilities": ["sql.query"],
                           "final_state": "SUCCESS"})
        final = _final("plan", ["sql.query"], step_status="failed")
        r = _eval_graph_case(case, case.expected, final)
        assert r.status == "fail"
        assert "终态期望 SUCCESS，实际 FAILED" in r.error_msg
