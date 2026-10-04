"""发布回归门禁回归测试（2026-10-04 验收收敛一期）。

覆盖验收项：GATE-11（baseline 回归门）、GATE-12（最低样本量门）、
DATA-08/09（极小样本不因 100% pass 放行）、GATE-03（RAGAS 双门禁）、
GATE-16（blocked_rules 结构化）、REG-03/04（逐样本回归/提升清单）、
REG-06（分桶回归）、REG-10（baseline unavailable 显式口径）。
"""
from __future__ import annotations

import json

import pytest

from backend.evaluation.gate import regression as reg
from backend.evaluation.models import EvalReport, EvalResult, ModuleSummary, TestCase
from backend.evaluation.prompt_release_runner import (
    _assemble_release_gate,
    _evaluate_ragas_gate,
)
from backend.evaluation.prompt_release_runner import _run_sync  # noqa: F401 导入存在性


# ── 测试替身构造 ──────────────────────────────────────────────


class _Summary:
    def __init__(self, metrics=None, pass_rate=1.0, total=10, errors=0, skipped=0):
        self.metrics = metrics or {}
        self.pass_rate = pass_rate
        self.total = total
        self.errors = errors
        self.skipped = skipped


class _Tier:
    def __init__(self, passed=True, pass_rate=1.0, threshold=0.85, tier="core"):
        self.passed_threshold = passed
        self.pass_rate = pass_rate
        self.threshold = threshold
        self.tier = tier
        self.gate_reasons = [] if passed else [f"通过率 {pass_rate:.1%} 低于阈值"]


class _Release:
    def __init__(self, dataset_provenance=None):
        self.dataset_provenance = dataset_provenance or {}


def _report(metadata=None, tier_summaries=None):
    return type("R", (), {
        "metadata": metadata or {},
        "tier_summaries": tier_summaries or [_Tier()],
    })()


@pytest.fixture
def baseline_root(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "BASELINE_ROOT", tmp_path)
    return tmp_path


def _write_baseline(module: str, version: str, payload: dict) -> None:
    path = reg.BASELINE_ROOT / f"baseline_{module}_{version}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# ── GATE-11 / C1-1：结构化回归门 ─────────────────────────────


def test_regression_gate_detects_metric_drop(baseline_root):
    _write_baseline("rag", "5.0", {
        "module": "rag", "dataset_version": "5.0", "pass_rate": 0.9,
        "metrics": {"recall@5": 0.8},
        "prompt_versions": {},
    })
    result = reg.evaluate_regression_gate(
        "rag", "5.0",
        {"recall@5": 0.6, "recall@10": 0.7},
        0.85,
    )
    assert result["baseline_available"] is True
    assert result["regression_pass"] is False
    metric_names = {e["metric"] for e in result["errors"]}
    assert "recall@5" in metric_names
    assert "pass_rate" in metric_names  # 0.9→0.85 降 5% 触界（阈值默认 5%）


def test_regression_gate_pass_when_stable(baseline_root):
    _write_baseline("rag", "5.0", {
        "module": "rag", "dataset_version": "5.0", "pass_rate": 0.9,
        "metrics": {"recall@5": 0.8},
        "prompt_versions": {},
    })
    result = reg.evaluate_regression_gate(
        "rag", "5.0", {"recall@5": 0.79, "recall@10": 0.7}, 0.9,
    )
    assert result["regression_pass"] is True
    assert result["errors"] == []


# ── REG-10 / C9-3：无 baseline 显式口径，不伪造 delta ────────


def test_regression_gate_baseline_unavailable_explicit(baseline_root):
    result = reg.evaluate_regression_gate("rag", "no-such-version", {"recall@5": 0.9}, 1.0)
    assert result["baseline_available"] is False
    assert result["regression_pass"] is None
    assert any("baseline_unavailable" in m for m in result["messages"])


# ── REG-03/04 / C1-3：逐样本回归/提升清单 ────────────────────


def _mk_report(statuses: dict[str, str]) -> EvalReport:
    results = [
        EvalResult(case_id=cid, module="rag", status=st, expected={}, actual={})
        for cid, st in statuses.items()
    ]
    summary = ModuleSummary(
        module="rag", total=len(results), passed=1, failed=1,
        errors=0, skipped=0, pass_rate=0.5, metrics={},
    )
    return EvalReport(
        module="rag", mode="offline", summaries=[summary], results=results,
        metadata={},
    )


def test_diff_samples_lists_regression_and_improvement():
    base = _mk_report({"A": "pass", "B": "fail", "C": "pass"})
    cur = _mk_report({"A": "fail", "B": "pass", "C": "pass"})
    diff = reg.diff_samples(cur, base)
    assert [s["case_id"] for s in diff["regression_samples"]] == ["A"]
    assert diff["regression_samples"][0]["baseline_status"] == "pass"
    assert diff["regression_samples"][0]["current_status"] == "fail"
    assert [s["case_id"] for s in diff["improvement_samples"]] == ["B"]
    assert diff["compared"] == 3


def test_diff_samples_no_common_cases_explicit_note():
    diff = reg.diff_samples(_mk_report({"A": "pass"}), _mk_report({"X": "pass"}))
    assert diff["compared"] == 0
    assert "baseline_unavailable" in diff["note"]


# ── REG-06 / C1-5：分桶回归 ──────────────────────────────────


def test_bucket_regression_overall_up_single_domain_down():
    baseline_buckets = {
        "by_domain": {
            "order": {"total": 10, "pass_rate": 0.9},
            "policy": {"total": 5, "pass_rate": 0.8},
        },
    }
    current_buckets = {
        "by_domain": {
            "order": {"total": 10, "pass_rate": 0.5},
            "policy": {"total": 5, "pass_rate": 1.0},
        },
    }
    regressions = reg.compare_buckets(current_buckets, baseline_buckets)
    assert len(regressions) == 1
    assert regressions[0]["bucket"] == "order"
    assert regressions[0]["severity"] == "error"


def test_bucket_regression_small_buckets_ignored():
    baseline_buckets = {"by_domain": {"tiny": {"total": 2, "pass_rate": 1.0}}}
    current_buckets = {"by_domain": {"tiny": {"total": 2, "pass_rate": 0.0}}}
    assert reg.compare_buckets(current_buckets, baseline_buckets) == []


# ── GATE-12 / DATA-09 / C1-2：最低样本量门 ───────────────────


def test_min_samples_gate_blocks_tiny_perfect_suite():
    report = _report(
        metadata={"suite_governance": {"min_samples": 8}},
        tier_summaries=[_Tier(passed=True, pass_rate=1.0)],
    )
    gate = _assemble_release_gate(
        report, _Summary(total=2, errors=0, skipped=0, pass_rate=1.0), _Release(),
    )
    assert gate["sample_pass"] is False
    assert gate["tier_pass"] is True
    rules = [r["rule"] for r in gate["blocked_rules"]]
    assert "min_samples" in rules
    entry = next(r for r in gate["blocked_rules"] if r["rule"] == "min_samples")
    assert entry["expected"] == ">= 8"
    assert entry["actual"] == "2"


def test_min_samples_gate_passes_sufficient_suite():
    report = _report(
        metadata={"suite_governance": {"min_samples": 8}},
        tier_summaries=[_Tier(passed=True)],
    )
    gate = _assemble_release_gate(
        report, _Summary(total=8, errors=0, skipped=0, pass_rate=1.0), _Release(),
    )
    assert gate["sample_pass"] is True
    assert gate["blocked_rules"] == []


def test_tier_evaluator_min_samples_field_snapshot():
    from backend.evaluation.gate.evaluator import evaluate_tiers

    cases = [TestCase(id=f"c{i}", question="q", module="rag", expected={},
                      metadata={"tier": "core"}) for i in range(3)]
    results = [
        EvalResult(case_id=c.id, module="rag", status="pass", expected={}, actual={})
        for c in cases
    ]
    summaries = evaluate_tiers(cases, results, min_samples=8)
    # 3 条全过但有效样本 < 8 → 样本量门失败且原因落快照
    assert all(s.passed_min_samples is False for s in summaries)
    assert summaries[0].valid_samples == 3
    assert summaries[0].min_samples == 8
    assert any("有效样本不足" in r for r in summaries[0].gate_reasons)


# ── GATE-16 / C1-6：blocked_rules 结构化 ─────────────────────


def test_blocked_rules_cover_tier_and_samples():
    report = _report(
        metadata={},
        tier_summaries=[
            _Tier(passed=False, pass_rate=0.5, tier="core"),
            _Tier(passed=True, tier="smoke"),
        ],
    )
    gate = _assemble_release_gate(
        report, _Summary(total=2, errors=0, skipped=0, pass_rate=0.5), _Release(),
    )
    rules = {r["rule"] for r in gate["blocked_rules"]}
    assert rules == {"tier_threshold", "min_samples"}
    for r in gate["blocked_rules"]:
        assert {"rule", "expected", "actual", "severity"} <= set(r)


# ── GATE-03 / C1-7：RAGAS 双门禁 ─────────────────────────────


def test_ragas_gate_not_executed_is_fail_closed():
    gate = _evaluate_ragas_gate(_report(), _Summary(metrics={}))
    assert gate["ragas_pass"] is False
    assert gate["rule"] == "ragas_not_executed"


def test_ragas_gate_threshold_violation_detected():
    metrics = {
        "ragas_faithfulness": 0.7, "ragas_answer_relevancy": 0.9,
        "ragas_context_precision": 0.9, "ragas_context_recall": 0.9,
    }
    gate = _evaluate_ragas_gate(
        _report(metadata={"ragas_samples": {"valid": 8, "invalid": 0}}),
        _Summary(metrics=metrics),
    )
    assert gate["ragas_pass"] is False
    assert "ragas_faithfulness" in gate["actual"]


def test_ragas_gate_valid_ratio_degraded():
    metrics = {k: 0.95 for k in (
        "ragas_faithfulness", "ragas_answer_relevancy",
        "ragas_context_precision", "ragas_context_recall",
    )}
    gate = _evaluate_ragas_gate(
        _report(metadata={"ragas_samples": {"valid": 7, "invalid": 3}}),
        _Summary(metrics=metrics),
    )
    assert gate["degraded"] is True
    assert gate["ragas_pass"] is False


def test_ragas_gate_pass_all_thresholds():
    metrics = {k: 0.95 for k in (
        "ragas_faithfulness", "ragas_answer_relevancy",
        "ragas_context_precision", "ragas_context_recall",
    )}
    gate = _evaluate_ragas_gate(
        _report(metadata={"ragas_samples": {"valid": 10, "invalid": 0}}),
        _Summary(metrics=metrics),
    )
    assert gate["ragas_pass"] is True
