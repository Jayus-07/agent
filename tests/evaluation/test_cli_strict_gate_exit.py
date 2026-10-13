"""--strict-gate 退出码回归测试（2026-10-11 GPT 最终验收 P0 修复）。

背景：cli.py 原退出码只检查 tier_summaries.passed_threshold——分层通过率
达标时即使完整 Release Gate 判 FAIL（required 指标缺失/不达标、GATE-12
样本量不足）或 INVALID 也 exit 0。修复后 strict-gate 模式复用唯一裁决出口
compute_module_release_gate：PASS=0 / FAIL=1 / INVALID=3。

本文件用构造的 EvalReport 直接驱动 cli._strict_gate_exit_code（不发起真实
评测、无付费调用）。
"""
from __future__ import annotations

import pytest

from backend.evaluation.cli import _strict_gate_exit_code
from backend.evaluation.gate import evaluate_tiers
from backend.evaluation.models import EvalReport, EvalResult, ModuleSummary, TestCase


def _cases(tiers: list[str]) -> list[TestCase]:
    return [
        TestCase(id=f"RC-{i:03d}", question=f"问题{i}", module="rag", metadata={"tier": t})
        for i, t in enumerate(tiers, start=1)
    ]


def _pass_results(cases: list[TestCase], metrics: dict | None = None) -> list[EvalResult]:
    return [
        EvalResult(
            case_id=c.id, module="rag", status="pass", expected={}, actual={},
            metrics=dict(metrics or {}),
        )
        for c in cases
    ]


def _report(pass_rate: float, tier_summaries, results, metrics: dict | None = None):
    summary = ModuleSummary(
        module="rag", total=29, passed=round(pass_rate * 29), failed=29 - round(pass_rate * 29),
        errors=0, skipped=0, pass_rate=pass_rate,
        metrics=dict(metrics or {
            "recall@5": 0.9, "mrr": 0.9,
            "reject_accuracy": 1.0, "false_answer_rate": 0.0,
        }),
    )
    return EvalReport(
        module="rag", mode="offline",
        summaries=[summary], results=results,
        tier_summaries=tier_summaries, metadata={"evaluator_mode": "self_built"},
    )


def _healthy_tier_and_results():
    tiers = ["smoke"] * 19 + ["hard"] * 10
    cases = _cases(tiers)
    results = _pass_results(cases)
    return cases, results, evaluate_tiers(cases, results, min_samples=20)


def test_strict_gate_pass_exit_0():
    """质量达标 + 分层达标 + 套件样本量达标 → exit 0。"""
    cases, results, tiers = _healthy_tier_and_results()
    report = _report(1.0, tiers, results)
    assert _strict_gate_exit_code(report, []) == 0


def test_required_metric_fail_exit_1():
    """必需指标不达标（recall@5 < 0.8）→ Gate FAIL → exit 1（即使分层全过）。"""
    cases, results, tiers = _healthy_tier_and_results()
    # 分层"通过"用例（per-case pass），但 required 指标 recall@5 不达标
    results = _pass_results(cases, metrics={"recall@5": 0.5, "mrr": 0.9})
    report = _report(1.0, tiers, results, metrics={"recall@5": 0.5, "mrr": 0.9})
    assert _strict_gate_exit_code(report, []) == 1


def test_min_valid_samples_fail_exit_1():
    """P0 反向场景：分层通过率达标（passed_threshold 全 True）但套件有效
    样本量不足（GATE-12）→ Gate FAIL → exit 1，不得 exit 0。"""
    tiers = ["smoke"] * 19 + ["hard"] * 10
    cases = _cases(tiers)
    results = _pass_results(cases)
    tiers = evaluate_tiers(cases, results, min_samples=100)  # 套件 29 < 100
    assert all(ts.passed_threshold for ts in tiers)  # 分层通过率确实达标
    assert any(not ts.passed_min_samples for ts in tiers)  # 但样本量门未过
    report = _report(1.0, tiers, results)
    assert _strict_gate_exit_code(report, []) == 1


def test_tier_threshold_fail_exit_1():
    """分层通过率低于阈值 → exit 1（strict 与旧行为一致）。"""
    tiers = ["smoke"] * 19 + ["hard"] * 10
    cases = _cases(tiers)
    results = [
        EvalResult(case_id=c.id, module="rag",
                   status="pass" if i % 4 else "fail", expected={}, actual={})
        for i, c in enumerate(cases)
    ]
    tiers = evaluate_tiers(cases, results, min_samples=20)
    failed = [ts for ts in tiers if not ts.passed_threshold]
    assert failed  # 3/4 通过率必挂 smoke 95% 阈值
    report = _report(0.75, tiers, results)
    assert _strict_gate_exit_code(report, failed) == 1


def test_invalid_exit_3():
    """run 有效性失败（零结果 → INVALID）→ exit 3，与质量 FAIL 区分。"""
    cases, results, tiers = _healthy_tier_and_results()
    report = _report(1.0, tiers, results=[])  # 空 results → INVALID_DATASET
    assert _strict_gate_exit_code(report, []) == 3
