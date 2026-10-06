"""模块感知发布门禁 + Run 有效性回归测试（P0-01/P0-04）。

验收锚点：
- SQL 5/45 (11.1%) 必须 FAIL（旧逻辑 fail-open 判 PASS——P0 回归）
- SQL offline 42/45 (93.3%) < required 95% → FAIL（数据缺失/不达标不假装成功）
- request_fallbacks 全挂 run → INVALID（环境故障不假装质量失败）
- SQL report 中 RAG 专属指标 → N/A，不影响门禁（模块不适用不假装缺指标）
"""
from __future__ import annotations

import math

import pytest

from backend.evaluation.models import EvalReport, EvalResult, ModuleSummary, TierSummary
from backend.evaluation.release_gate import (
    DEFAULT_THRESHOLDS,
    GateVerdict,
    compute_metrics_gate,
    compute_module_release_gate,
    policy_for,
)
from backend.evaluation.report.builder import compute_release_gate
from backend.evaluation.validity import (
    RunValidity,
    classify_report_validity,
    validity_from_report_metadata,
)


# ── 测试替身构造 ──────────────────────────────────────────────────────────


def _result(
    case_id: str,
    status: str,
    module: str = "sql",
    metrics: dict | None = None,
    error_msg: str | None = None,
    actual: dict | None = None,
) -> EvalResult:
    return EvalResult(
        case_id=case_id, module=module, status=status,
        expected={}, actual=actual or {}, metrics=metrics or {},
        error_msg=error_msg,
    )


def _summary(
    module: str, total: int, passed: int, failed: int = 0,
    errors: int = 0, skipped: int = 0, metrics: dict | None = None,
) -> ModuleSummary:
    return ModuleSummary(
        module=module, total=total, passed=passed, failed=failed,
        errors=errors, skipped=skipped,
        pass_rate=(passed / total) if total else 0.0,
        metrics=metrics or {},
    )


def _report(
    module: str,
    summaries: list[ModuleSummary],
    results: list[EvalResult],
    metadata: dict | None = None,
    tier_summaries: list[TierSummary] | None = None,
) -> EvalReport:
    return EvalReport(
        module=module, mode="live", summaries=summaries,
        results=results, metadata=metadata or {},
        tier_summaries=tier_summaries or [],
    )


def _sql_live_5_of_45() -> EvalReport:
    """A2 锚点：SQL live 5/45（真实质量失败）。"""
    results = [
        _result(f"SC-{i:03d}", "pass" if i < 5 else "fail")
        for i in range(45)
    ]
    return _report(
        "sql",
        [_summary("sql", 45, 5, failed=40, metrics={
            "router_hit": 0.556, "safety_pass": 0.111, "execution_match": 0.111,
        })],
        results,
    )


def _sql_budget_all_fail() -> EvalReport:
    """A3 锚点：request_fallbacks 全挂——环境无效，不是质量 0%。"""
    results = [
        _result(
            f"SC-{i:03d}", "fail",
            error_msg="查询失败 status=failed: 查询失败: 请求预算已超限: request_fallbacks",
            actual={"status": "failed",
                    "error": "查询失败: 请求预算已超限: request_fallbacks"},
        )
        for i in range(45)
    ]
    return _report(
        "sql",
        [_summary("sql", 45, 0, failed=45, metrics={
            "router_hit": 0.0, "safety_pass": 0.0,
        })],
        results,
    )


def _rag_golden_like() -> EvalReport:
    """D5 锚点：RAG 黄金集指标正常 → PASS。"""
    return _report(
        "rag",
        [_summary("rag", 99, 99, metrics={
            "pass_rate": 1.0, "recall@5": 0.92, "mrr": 0.89,
            "reject_accuracy": 1.0, "false_answer_rate": 0.0,
            "top1_accuracy": 0.85, "multi_hop_success": 0.75,
        })],
        [_result(f"RC-{i:03d}", "pass", module="rag") for i in range(99)],
    )


# ── Release Gate：模块感知 + fail-closed ─────────────────────────────────


def test_release_gate_sql_live_fail_not_pass():
    """D1：SQL 5/45 必须 FAIL（旧逻辑恒 PASS 的 P0 回归）。"""
    gate = compute_module_release_gate(_sql_live_5_of_45())
    assert gate["verdict"] == GateVerdict.FAIL.value
    assert gate["overall"] is False
    failed = {it["metric"] for it in gate["items"] if it["passed"] is False}
    assert {"pass_rate", "safety_pass", "execution_match"} <= failed


def test_release_gate_sql_offline_fail_closed():
    """SQL offline 42/45=93.3% < required 95% → FAIL；execution_match 缺失 → missing_required。"""
    report = _report(
        "sql",
        [_summary("sql", 45, 42, failed=3, metrics={
            "router_hit": 1.0, "safety_pass": 1.0,
        })],
        [_result(f"SC-{i:03d}", "pass" if i < 42 else "fail") for i in range(45)],
    )
    gate = compute_module_release_gate(report)
    assert gate["verdict"] == GateVerdict.FAIL.value
    by_metric = {it["metric"]: it for it in gate["items"]}
    assert by_metric["pass_rate"]["passed"] is False
    assert by_metric["execution_match"]["status"] == "missing_required"
    assert by_metric["execution_match"]["passed"] is False


def test_release_gate_required_metric_missing():
    """RAG required 指标（mrr）缺失 → FAIL，禁止 None→continue→PASS。"""
    report = _report(
        "rag",
        [_summary("rag", 10, 10, metrics={
            "pass_rate": 1.0, "recall@5": 0.9,
            "reject_accuracy": 1.0, "false_answer_rate": 0.0,
        })],
        [_result(f"RC-{i:03d}", "pass", module="rag") for i in range(10)],
    )
    gate = compute_module_release_gate(report)
    assert gate["verdict"] == GateVerdict.FAIL.value
    mrr_item = next(it for it in gate["items"] if it["metric"] == "mrr")
    assert mrr_item["passed"] is False
    assert mrr_item["status"] == "missing_required"


def test_release_gate_nan_metric_is_missing_required():
    """NaN/Inf 是「数据缺失」不是合法值——required 指标为 NaN → FAIL。"""
    report = _report(
        "rag",
        [_summary("rag", 10, 10, metrics={
            "pass_rate": 1.0, "recall@5": 0.9, "mrr": float("nan"),
            "reject_accuracy": 1.0, "false_answer_rate": 0.0,
        })],
        [_result(f"RC-{i:03d}", "pass", module="rag") for i in range(10)],
    )
    gate = compute_module_release_gate(report)
    assert gate["verdict"] == GateVerdict.FAIL.value


def test_release_gate_not_applicable_display():
    """D4：SQL report 的 RAG 专属指标 → N/A，不影响门禁。"""
    gate = compute_module_release_gate(_sql_live_5_of_45())
    na = {
        it["metric"]: it
        for it in gate["items"]
        if it["requirement"] == "not_applicable"
    }
    for metric in ("recall@5", "mrr", "ragas_faithfulness", "citation_accuracy"):
        assert metric in na, f"SQL report 缺少 {metric} 的 N/A 项"
        assert na[metric]["passed"] is None
        assert na[metric]["status"] == "not_applicable"
    # N/A 不改变裁决：verdict 由 required 决定（此 report 为 FAIL，非 N/A 所致）


def test_release_gate_rag_golden_pass():
    """D5：RAG 黄金集指标正常 → PASS（self 模式，ragas 缺席为 N/A）。"""
    gate = compute_module_release_gate(_rag_golden_like())
    assert gate["verdict"] == GateVerdict.PASS.value
    assert gate["overall"] is True
    ragas_items = [it for it in gate["items"] if it["metric"].startswith("ragas_")]
    assert ragas_items and all(it["status"] == "not_applicable" for it in ragas_items)


def test_release_gate_ragas_mode_promotes_to_required():
    """evaluator_mode 含 ragas → ragas 指标升级 required；缺失 → FAIL（fail-closed）。"""
    report = _rag_golden_like()
    report.metadata["evaluator_mode"] = "self+ragas"
    gate = compute_module_release_gate(report)
    assert gate["verdict"] == GateVerdict.FAIL.value
    missing = [it for it in gate["items"] if it["status"] == "missing_required"]
    assert missing and all(it["metric"].startswith("ragas_") for it in missing)


def test_release_gate_invalid_environment():
    """D2/D3：request_fallbacks 全挂 → INVALID（不是 FAIL、更不是 PASS）。"""
    gate = compute_module_release_gate(_sql_budget_all_fail())
    assert gate["verdict"] == GateVerdict.INVALID.value
    assert gate["invalid_reason"] == "request_budget_exhausted"
    assert gate["validity"]["validity"] == RunValidity.INVALID_BUDGET.value
    assert gate["overall"] is False


def test_release_gate_tier_gate_fail():
    """层级门禁：tier_summaries 存在的层不达标 → FAIL。"""
    report = _rag_golden_like()
    report.tier_summaries = [TierSummary(
        tier="hard", total=10, passed=6, failed=4,
        pass_rate=0.6, threshold=0.85, passed_threshold=False,
    )]
    gate = compute_module_release_gate(report)
    assert gate["verdict"] == GateVerdict.FAIL.value
    tier_item = next(it for it in gate["items"] if it["metric"] == "tier_gates")
    assert tier_item["passed"] is False


def test_release_gate_module_all_aggregates_worst():
    """module="all"：逐模块裁决，verdict 取最差。"""
    report = _report(
        "all",
        [
            _summary("rag", 10, 10, metrics={
                "pass_rate": 1.0, "recall@5": 0.9, "mrr": 0.9,
                "reject_accuracy": 1.0, "false_answer_rate": 0.0,
            }),
            _summary("sql", 45, 5, failed=40, metrics={
                "router_hit": 0.5, "safety_pass": 0.1, "execution_match": 0.1,
            }),
        ],
        [_result("R-1", "pass", module="rag"), _result("S-1", "fail", module="sql")],
    )
    gate = compute_module_release_gate(report)
    assert gate["verdict"] == GateVerdict.FAIL.value
    modules = {it["module"] for it in gate["items"]}
    assert {"rag", "sql"} <= modules


def test_policy_for_unknown_module_fail_closed():
    """未知模块兜底策略 = pass_rate required（fail-closed，不空判）。"""
    policy = policy_for("future_module")
    assert any(r.metric == "pass_rate" for r in policy.required)


# ── 旧入口兼容：builder.compute_release_gate 委托后行为收敛 ─────────────


def test_legacy_compute_release_gate_sql_no_longer_passes():
    """旧签名下 SQL 指标面不再恒 PASS（旧实现 None→continue→True 的 P0 回归）。"""
    sql_metrics = {"router_hit": 0.556, "safety_pass": 0.111, "execution_match": 0.111}
    result = compute_release_gate(sql_metrics)
    assert result["overall"] is False
    assert result["verdict"] == GateVerdict.FAIL.value


def test_legacy_compute_release_gate_rag_still_passes():
    rag_metrics = {
        "pass_rate": 1.0, "recall@5": 0.92, "mrr": 0.89,
        "reject_accuracy": 1.0, "false_answer_rate": 0.0,
    }
    result = compute_release_gate(rag_metrics)
    assert result["overall"] is True


def test_legacy_gate_accepts_threshold_override():
    rag_metrics = {"pass_rate": 1.0, "recall@5": 0.85, "mrr": 0.80,
                   "reject_accuracy": 1.0, "false_answer_rate": 0.0}
    overridden = {**DEFAULT_THRESHOLDS, "recall@5": 0.90}
    assert compute_release_gate(rag_metrics, overridden)["overall"] is False
    assert compute_release_gate(rag_metrics)["overall"] is True


# ── Run Validity（P0-04） ────────────────────────────────────────────────


def test_validity_budget_exhausted_run_is_invalid():
    verdict = classify_report_validity(_sql_budget_all_fail())
    assert verdict.validity is RunValidity.INVALID_BUDGET
    assert verdict.invalid_reason == "request_budget_exhausted"
    assert not verdict.is_valid


def test_validity_real_quality_failure_is_valid():
    """真实质量失败（错误面无基础设施签名）→ VALID，显示真实 0%/低分。"""
    report = _report(
        "sql",
        [_summary("sql", 10, 0, failed=10)],
        [
            _result(f"SC-{i:03d}", "fail",
                    error_msg="生成 SQL 结果集与 gold_sql 不一致")
            for i in range(10)
        ],
    )
    assert classify_report_validity(report).validity is RunValidity.VALID


def test_validity_all_errored_is_infra_invalid():
    report = _report(
        "rag",
        [_summary("rag", 5, 0, errors=5)],
        [_result(f"RC-{i:03d}", "error", module="rag", error_msg="connection refused")
         for i in range(5)],
    )
    verdict = classify_report_validity(report)
    assert verdict.validity is RunValidity.INVALID_INFRA


def test_validity_few_transient_failures_stay_valid():
    """45 条里 2 条限流抖动 → 低于门槛，仍是 VALID（不把抖动放大成环境无效）。"""
    results = [
        _result(f"SC-{i:03d}", "fail", error_msg="生成 SQL 结果集与 gold_sql 不一致")
        for i in range(43)
    ]
    results += [
        _result("SC-100", "error", error_msg="Rate limit reached"),
        _result("SC-101", "error", error_msg="rate limit reached"),
    ]
    report = _report("sql", [_summary("sql", 45, 0, failed=43, errors=2)], results)
    assert classify_report_validity(report).validity is RunValidity.VALID


def test_validity_empty_results_is_dataset_invalid():
    report = _report("rag", [_summary("rag", 0, 0)], [])
    verdict = classify_report_validity(report)
    assert verdict.validity is RunValidity.INVALID_DATASET
    assert verdict.invalid_reason == "empty_results"


def test_validity_metadata_roundtrip():
    verdict = classify_report_validity(_sql_budget_all_fail())
    metadata = {"run_validity": verdict.as_dict()}
    restored = validity_from_report_metadata(metadata)
    assert restored is not None
    assert restored.validity is RunValidity.INVALID_BUDGET
    assert restored.invalid_reason == "request_budget_exhausted"
    # gate 优先消费落盘口径，不再现算
    report = _sql_budget_all_fail()
    report.metadata.update(metadata)
    gate = compute_module_release_gate(report, validity=None)
    assert gate["verdict"] == GateVerdict.INVALID.value


# ── JSON 报告渲染 ────────────────────────────────────────────────────────


def test_json_report_carries_three_state_verdict(tmp_path):
    from backend.evaluation.report.json import write_json_report

    path = write_json_report(_sql_budget_all_fail(), tmp_path)
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["release_gate"]["verdict"] == "INVALID"
    assert data["release_gate"]["invalid_reason"] == "request_budget_exhausted"
    assert data["release_gate"]["overall"] is False
