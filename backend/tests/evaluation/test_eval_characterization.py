"""评测框架特征测试 — 回归基准快照。

Phase 0 安全网：对 _build_summary / _build_tier_summaries / EvalReport 的
结构和数值行为建立快照断言。后续每个 Phase 完成后跑此测试，
确保核心聚合逻辑不发生行为漂移。

零外部依赖：使用构造的 mock 数据，不调用真实 RAG pipeline。
"""

import math

import pytest

from backend.evaluation.models import (
    EvalReport,
    EvalResult,
    ModuleSummary,
    TestCase,
    TierSummary,
)
from backend.evaluation.service import (
    _build_summary,
    _filter_cases_by_tier,
    _skip_results,
    _error_results,
)
from backend.evaluation.gate import TIER_THRESHOLDS, evaluate_tiers as _build_tier_summaries
from backend.evaluation.runner import evaluate_planner_offline


# ── Fixtures ──────────────────────────────────────────────────

def _make_result(
    case_id: str,
    status: str = "pass",
    metrics: dict | None = None,
    module: str = "rag",
) -> EvalResult:
    return EvalResult(
        case_id=case_id,
        module=module,
        status=status,
        expected={},
        actual={},
        metrics=metrics or {},
    )


def _make_case(
    case_id: str,
    tier: str | None = None,
    module: str = "rag",
) -> TestCase:
    meta = {}
    if tier:
        meta["tier"] = tier
    return TestCase(
        id=case_id,
        question=f"Q: {case_id}",
        module=module,
        metadata=meta,
    )


# ── _build_summary 特征 ──────────────────────────────────────

class TestBuildSummaryCharacterization:
    """快照 _build_summary 的结构和数值行为。"""

    def test_empty_results(self):
        summary = _build_summary([], "rag")
        assert summary.module == "rag"
        assert summary.total == 0
        assert summary.passed == 0
        assert summary.failed == 0
        assert summary.errors == 0
        assert summary.skipped == 0
        assert summary.pass_rate == 0.0
        assert summary.metrics == {}

    def test_all_pass(self):
        results = [_make_result(f"C{i}", "pass") for i in range(5)]
        summary = _build_summary(results, "rag")
        assert summary.total == 5
        assert summary.passed == 5
        assert summary.failed == 0
        assert summary.pass_rate == 1.0

    def test_mixed_status_counts(self):
        results = [
            _make_result("C1", "pass"),
            _make_result("C2", "fail"),
            _make_result("C3", "error"),
            _make_result("C4", "skip"),
            _make_result("C5", "pass"),
        ]
        summary = _build_summary(results, "rag")
        assert summary.total == 5
        assert summary.passed == 2
        assert summary.failed == 1
        assert summary.errors == 1
        assert summary.skipped == 1
        assert summary.pass_rate == 0.4

    def test_metric_aggregation_mean(self):
        results = [
            _make_result("C1", "pass", {"recall": 0.8, "precision": 0.6}),
            _make_result("C2", "pass", {"recall": 0.6, "precision": 0.4}),
        ]
        summary = _build_summary(results, "rag")
        assert summary.metrics["recall"] == 0.7
        assert summary.metrics["precision"] == 0.5

    def test_nan_excluded_from_aggregation(self):
        results = [
            _make_result("C1", "pass", {"recall": 0.8}),
            _make_result("C2", "pass", {"recall": float("nan")}),
            _make_result("C3", "pass", {"recall": 0.6}),
        ]
        summary = _build_summary(results, "rag")
        assert summary.metrics["recall"] == 0.7

    def test_metric_keys_union(self):
        results = [
            _make_result("C1", "pass", {"recall": 0.8}),
            _make_result("C2", "pass", {"precision": 0.6}),
        ]
        summary = _build_summary(results, "rag")
        assert "recall" in summary.metrics
        assert "precision" in summary.metrics

    def test_pass_rate_rounding(self):
        results = [
            _make_result("C1", "pass"),
            _make_result("C2", "pass"),
            _make_result("C3", "fail"),
        ]
        summary = _build_summary(results, "rag")
        assert summary.pass_rate == 0.6667

    def test_expected_metric_keys_for_rag(self):
        """快照：典型 RAG 结果应产出的 metric key 集合。"""
        results = [
            _make_result("C1", "pass", {
                "recall_at_5": 0.8,
                "precision_at_5": 0.6,
                "mrr": 0.7,
                "ndcg_at_5": 0.75,
                "sem_context_recall": 0.85,
                "sem_faithfulness": 0.9,
            }),
        ]
        summary = _build_summary(results, "rag")
        expected_keys = {
            "recall_at_5", "precision_at_5", "mrr", "ndcg_at_5",
            "sem_context_recall", "sem_faithfulness",
        }
        assert set(summary.metrics.keys()) == expected_keys


# ── _build_tier_summaries 特征 ────────────────────────────────

class TestBuildTierSummariesCharacterization:
    """快照 _build_tier_summaries 的结构和阈值行为。"""

    def test_no_tier_defaults_to_core(self):
        cases = [_make_case("C1"), _make_case("C2")]
        results = [_make_result("C1", "pass"), _make_result("C2", "fail")]
        tiers = _build_tier_summaries(cases, results)
        assert len(tiers) == 1
        assert tiers[0].tier == "core"
        assert tiers[0].total == 2
        assert tiers[0].passed == 1
        assert tiers[0].pass_rate == 0.5

    def test_multiple_tiers(self):
        cases = [
            _make_case("C1", "smoke"),
            _make_case("C2", "smoke"),
            _make_case("C3", "core"),
            _make_case("C4", "hard"),
        ]
        results = [
            _make_result("C1", "pass"),
            _make_result("C2", "pass"),
            _make_result("C3", "fail"),
            _make_result("C4", "pass"),
        ]
        tiers = _build_tier_summaries(cases, results)
        tier_map = {t.tier: t for t in tiers}

        assert "smoke" in tier_map
        assert "core" in tier_map
        assert "hard" in tier_map
        assert "regression" not in tier_map

        assert tier_map["smoke"].pass_rate == 1.0
        assert tier_map["core"].pass_rate == 0.0
        assert tier_map["hard"].pass_rate == 1.0

    def test_threshold_values_match_config(self):
        cases = [_make_case("C1", "smoke")]
        results = [_make_result("C1", "pass")]
        tiers = _build_tier_summaries(cases, results)
        assert tiers[0].threshold == TIER_THRESHOLDS["smoke"]

    def test_passed_threshold_flag(self):
        cases = [_make_case("C1", "smoke"), _make_case("C2", "smoke")]
        results = [_make_result("C1", "pass"), _make_result("C2", "fail")]
        tiers = _build_tier_summaries(cases, results)
        assert tiers[0].pass_rate == 0.5
        assert tiers[0].threshold == 0.95
        assert tiers[0].passed_threshold is False

    def test_missing_case_treated_as_skip(self):
        cases = [_make_case("C1", "core"), _make_case("C2", "core")]
        results = [_make_result("C1", "pass")]
        tiers = _build_tier_summaries(cases, results)
        assert tiers[0].total == 2
        assert tiers[0].passed == 1

    def test_tier_order_is_fixed(self):
        cases = [
            _make_case("C1", "hard"),
            _make_case("C2", "smoke"),
            _make_case("C3", "regression"),
            _make_case("C4", "core"),
        ]
        results = [_make_result(c.id, "pass") for c in cases]
        tiers = _build_tier_summaries(cases, results)
        tier_names = [t.tier for t in tiers]
        assert tier_names == ["smoke", "core", "hard", "regression"]


# ── _filter_cases_by_tier 特征 ────────────────────────────────

class TestFilterCasesByTier:
    def test_all_returns_everything(self):
        cases = [_make_case("C1", "smoke"), _make_case("C2", "core")]
        assert len(_filter_cases_by_tier(cases, "all")) == 2

    def test_filter_by_specific_tier(self):
        cases = [
            _make_case("C1", "smoke"),
            _make_case("C2", "core"),
            _make_case("C3", "smoke"),
        ]
        filtered = _filter_cases_by_tier(cases, "smoke")
        assert len(filtered) == 2
        assert all(c.metadata["tier"] == "smoke" for c in filtered)

    def test_no_tier_treated_as_core(self):
        cases = [_make_case("C1"), _make_case("C2", "smoke")]
        filtered = _filter_cases_by_tier(cases, "core")
        assert len(filtered) == 1
        assert filtered[0].id == "C1"


# ── _skip_results / _error_results 特征 ───────────────────────

class TestSkipAndError:
    def test_skip_results(self):
        cases = [_make_case("C1"), _make_case("C2")]
        results = _skip_results(cases, "rag", "not registered")
        assert len(results) == 2
        assert all(r.status == "skip" for r in results)
        assert all(r.error_msg == "not registered" for r in results)

    def test_error_results(self):
        cases = [_make_case("C1")]
        results = _error_results(cases, "rag", "boom")
        assert len(results) == 1
        assert results[0].status == "error"
        assert results[0].error_msg == "boom"


# ── evaluate_planner_offline 特征 ─────────────────────────────

class TestPlannerOffline:
    def test_perfect_match(self):
        result = evaluate_planner_offline(
            "P001",
            {"capabilities": ["search", "summarize"]},
            ["search", "summarize"],
        )
        assert result.status == "pass"
        assert result.metrics["jaccard"] == 1.0
        assert result.metrics["redundancy"] == 0.0
        assert result.metrics["structure_ok"] == 1.0

    def test_partial_match(self):
        result = evaluate_planner_offline(
            "P002",
            {"capabilities": ["search", "summarize", "translate"]},
            ["search", "summarize"],
        )
        assert result.metrics["jaccard"] == pytest.approx(0.6667, abs=0.001)

    def test_redundancy_penalty(self):
        result = evaluate_planner_offline(
            "P003",
            {
                "capabilities": ["search"],
                "should_not_contain": ["delete", "drop"],
            },
            ["search", "delete"],
        )
        assert result.metrics["redundancy"] == 0.5

    def test_structure_check_with_edges(self):
        result = evaluate_planner_offline(
            "P004",
            {
                "capabilities": ["search", "summarize"],
                "edges": [{"from": "search", "to": "summarize"}],
            },
            ["search", "summarize"],
        )
        assert result.metrics["structure_ok"] == 1.0

    def test_structure_fail(self):
        result = evaluate_planner_offline(
            "P005",
            {
                "capabilities": ["search"],
                "edges": [{"from": "search", "to": "summarize"}],
            },
            ["search"],
        )
        assert result.metrics["structure_ok"] == 0.0
        assert result.status == "fail"


# ── EvalReport 结构快照 ──────────────────────────────────────

class TestEvalReportStructure:
    def test_report_fields(self):
        report = EvalReport(
            module="rag",
            mode="offline",
            summaries=[
                ModuleSummary(
                    module="rag", total=10, passed=8, failed=1,
                    errors=0, skipped=1, pass_rate=0.8,
                    metrics={"recall": 0.75},
                ),
            ],
            results=[],
            tier_summaries=[
                TierSummary(
                    tier="core", total=10, passed=8,
                    failed=2, pass_rate=0.8,
                    threshold=0.85, passed_threshold=False,
                ),
            ],
        )
        assert report.module == "rag"
        assert report.mode == "offline"
        assert len(report.summaries) == 1
        assert report.summaries[0].pass_rate == 0.8
        assert len(report.tier_summaries) == 1
        assert report.tier_summaries[0].passed_threshold is False
        assert report.total_score is None

    def test_report_serialization_roundtrip(self):
        report = EvalReport(
            module="all",
            mode="live",
            summaries=[],
            results=[],
            total_score=0.85,
            prompt_versions={"rag_prompt": 3},
        )
        data = report.model_dump()
        restored = EvalReport(**data)
        assert restored.total_score == 0.85
        assert restored.prompt_versions["rag_prompt"] == 3
