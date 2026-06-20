"""测试 evaluation/runner.py 的执行逻辑（离线模式）。"""
import pytest
from evaluation.runner import run_rag, evaluate_planner_offline
from evaluation.dataset import load_dataset
from evaluation.models import TestCase, EvalResult


class TestRunRag:
    @pytest.fixture
    def rag_cases(self):
        return load_dataset("rag")[:5]  # first 5 for quick test

    def test_returns_results(self, rag_cases):
        results = run_rag(rag_cases)
        assert len(results) == len(rag_cases)
        assert all(isinstance(r, EvalResult) for r in results)

    def test_result_has_expected_fields(self, rag_cases):
        results = run_rag(rag_cases)
        for r in results:
            assert r.case_id
            assert r.module == "rag"
            assert r.status in ("pass", "fail", "error", "skip")
            assert "recall@5" in r.metrics
            assert r.duration_ms >= 0

    def test_empty_cases(self):
        results = run_rag([])
        assert results == []


class TestEvaluatePlannerOffline:
    def test_exact_match_pass(self):
        result = evaluate_planner_offline(
            case_id="P001",
            expected={"capabilities": ["query_database"]},
            actual_capabilities=["query_database"],
        )
        assert result.metrics["jaccard"] == 1.0

    def test_partial_match(self):
        result = evaluate_planner_offline(
            case_id="P002",
            expected={"capabilities": ["query_database"], "should_not_contain": ["search_knowledge"]},
            actual_capabilities=["query_database", "search_knowledge"],
        )
        assert result.metrics["jaccard"] < 1.0
        assert result.metrics["redundancy"] > 0.0

    def test_should_not_contain_violation(self):
        result = evaluate_planner_offline(
            case_id="P003",
            expected={"capabilities": ["search_knowledge"], "should_not_contain": ["query_database"]},
            actual_capabilities=["search_knowledge", "query_database"],
        )
        assert result.status == "fail"
