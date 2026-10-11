"""RAG Regression 门禁机制回归测试（2026-10-11 CI 门禁修复）。

背景：main 上 RAG Regression 工作流自创建起 14 连败（10-01~10-10），其中两层
机制缺陷与业务质量无关，导致门禁无论质量如何永远 FAIL、失败原因失真：

1. release_gate.compute_module_release_gate 从 ModuleSummary.metrics 读
   pass_rate，但该键只在顶层字段 pass_rate 上（aggregate_metrics 只聚合
   per-case 指标）→ required pass_rate 恒 missing_required。
2. gate.evaluator.evaluate_tiers 把 suite 级 min_samples=20 下放到每层，
   regression 套件 smoke(19)+hard(10) 单层永远 < 20 → 样本量门数学死锁。

本文件锁定两个修复的行为：键错位不再发生、真实 pass_rate 参与判定（不放水）、
套件级样本量判定消除死锁且保留极少样本保护。
"""
from __future__ import annotations

from backend.evaluation.gate import evaluate_tiers
from backend.evaluation.models import EvalReport, EvalResult, ModuleSummary, TestCase
from backend.evaluation.release_gate import compute_module_release_gate


def _cases(tiers: list[str]) -> list[TestCase]:
    return [
        TestCase(id=f"RC-{i:03d}", question=f"问题{i}", module="rag", metadata={"tier": t})
        for i, t in enumerate(tiers, start=1)
    ]


def _pass_results(cases: list[TestCase]) -> list[EvalResult]:
    return [
        EvalResult(case_id=c.id, module="rag", status="pass", expected={}, actual={})
        for c in cases
    ]


def _rag_report(
    pass_rate: float,
    tier_summaries: list,
    results: list[EvalResult] | None = None,
) -> EvalReport:
    """metrics 全达标但不含 pass_rate 键（复现 aggregate_metrics 产物形态）。

    results 必须非空：validity 层对空结果 fail-closed 判 INVALID（那是另一
    条防线，不属于本文件两个修复的断言范围）。
    """
    summary = ModuleSummary(
        module="rag", total=29, passed=round(pass_rate * 29), failed=29 - round(pass_rate * 29),
        errors=0, skipped=0, pass_rate=pass_rate,
        metrics={
            "recall@5": 0.9, "mrr": 0.9,
            "reject_accuracy": 1.0, "false_answer_rate": 0.0,
        },
    )
    return EvalReport(
        module="rag", mode="offline",
        summaries=[summary],
        results=results if results is not None else _pass_results(_cases(["smoke"] * 29)),
        tier_summaries=tier_summaries, metadata={"evaluator_mode": "self_built"},
    )


def test_gate_pass_rate_read_from_summary_top_level():
    """pass_rate 只在 ModuleSummary 顶层字段时，gate 必须取到真实值而非判缺失。"""
    report = _rag_report(pass_rate=1.0, tier_summaries=[])
    gate = compute_module_release_gate(report)
    pass_rate_item = next(it for it in gate["items"] if it["metric"] == "pass_rate")
    assert pass_rate_item["value"] == 1.0
    assert pass_rate_item["passed"] is True
    assert pass_rate_item["status"] == "pass"
    assert gate["verdict"] == "PASS"


def test_gate_pass_rate_real_value_still_enforced():
    """修复不是放水：真实 pass_rate 低于阈值必须 FAIL，且原因带具体数值。"""
    report = _rag_report(pass_rate=0.8966, tier_summaries=[])
    gate = compute_module_release_gate(report)
    pass_rate_item = next(it for it in gate["items"] if it["metric"] == "pass_rate")
    assert pass_rate_item["value"] == 0.8966
    assert pass_rate_item["passed"] is False
    assert "0.8966" in pass_rate_item["reason"]
    assert gate["verdict"] == "FAIL"


def test_tier_min_samples_suite_level_no_deadlock():
    """smoke(19)+hard(10)、suite min_samples=20：套件总量达标，样本量门不得死锁。

    修复前：每层独立判 valid>=20，两层恒 False，无论质量多高 tier_gates 恒 FAIL。
    """
    tiers = ["smoke"] * 19 + ["hard"] * 10
    cases = _cases(tiers)
    results = _pass_results(cases)
    tier_summaries = evaluate_tiers(cases, results, min_samples=20)
    assert [ts.tier for ts in tier_summaries] == ["smoke", "hard"]
    for ts in tier_summaries:
        assert ts.pass_rate == 1.0
        assert ts.passed_min_samples is True, ts.gate_reasons
        assert not any("有效样本不足" in r for r in ts.gate_reasons)


def test_tier_min_samples_protection_kept():
    """极少样本保护保留：总有效样本不足时各层 fail-closed 并注明原因。"""
    tiers = ["smoke"] * 3 + ["hard"] * 2
    cases = _cases(tiers)
    results = _pass_results(cases)
    tier_summaries = evaluate_tiers(cases, results, min_samples=8)
    assert len(tier_summaries) == 2
    for ts in tier_summaries:
        assert ts.passed_min_samples is False
        assert any("有效样本不足" in r for r in ts.gate_reasons)


def test_full_gate_pass_with_tier_summaries():
    """端到端：质量达标 + 套件样本量达标 → verdict PASS（修复前恒 FAIL）。"""
    tiers = ["smoke"] * 19 + ["hard"] * 10
    cases = _cases(tiers)
    results = _pass_results(cases)
    tier_summaries = evaluate_tiers(cases, results, min_samples=20)
    report = _rag_report(pass_rate=1.0, tier_summaries=tier_summaries, results=results)
    gate = compute_module_release_gate(report)
    tier_item = next(it for it in gate["items"] if it["metric"] == "tier_gates")
    assert tier_item["passed"] is True, tier_item["reason"]
    assert gate["verdict"] == "PASS"
