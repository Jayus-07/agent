"""测试 evaluation/runner.py 的调度逻辑 + registry 分发。"""

import pytest
from evaluation.runner import evaluate_planner_offline, run_module, _skip_results, _error_results
from evaluation.registry import register_runner, get_runner, list_registered, clear_registry
from evaluation.dataset import load_dataset
from evaluation.models import TestCase, EvalResult


# ==================== Planner 离线评估（通用函数） ====================

class TestEvaluatePlannerOffline:
    def test_exact_match_pass(self):
        result = evaluate_planner_offline(
            case_id="P001",
            expected={"capabilities": ["query_database"]},
            actual_capabilities=["query_database"],
        )
        assert result.metrics["jaccard"] == 1.0
        assert result.status == "pass"

    def test_partial_match(self):
        result = evaluate_planner_offline(
            case_id="P002",
            expected={
                "capabilities": ["query_database"],
                "should_not_contain": ["search_knowledge"],
            },
            actual_capabilities=["query_database", "search_knowledge"],
        )
        assert result.metrics["jaccard"] < 1.0
        assert result.metrics["redundancy"] > 0.0

    def test_should_not_contain_violation(self):
        result = evaluate_planner_offline(
            case_id="P003",
            expected={
                "capabilities": ["search_knowledge"],
                "should_not_contain": ["query_database"],
            },
            actual_capabilities=["search_knowledge", "query_database"],
        )
        assert result.status == "fail"

    def test_edges_structure_ok(self):
        result = evaluate_planner_offline(
            case_id="P004",
            expected={
                "capabilities": ["query_database", "search_knowledge", "generate_report"],
                "edges": [
                    {"from": "query_database", "to": "generate_report"},
                    {"from": "search_knowledge", "to": "generate_report"},
                ],
            },
            actual_capabilities=["query_database", "search_knowledge", "generate_report"],
        )
        assert result.metrics["structure_ok"] == 1.0


# ==================== Registry 分发 ====================

class TestRegistryDispatch:
    def setup_method(self):
        """每个测试前清空注册表。"""
        clear_registry()

    def test_run_module_unregistered_returns_skip(self):
        """未注册的模块返回 skip。"""
        case = TestCase(id="X001", question="test", module="planner")
        results = run_module("planner", [case])
        assert len(results) == 1
        assert results[0].status == "skip"
        assert "No runner registered" in (results[0].error_msg or "")

    def test_run_module_needs_live_but_offline_returns_skip(self):
        """需要 live 但未启用 → skip。"""
        def dummy_runner(cases, **kwargs):
            return [
                EvalResult(case_id=c.id, module="planner", status="pass",
                           expected={}, actual={})
                for c in cases
            ]

        register_runner("planner", dummy_runner, needs_live=True)

        case = TestCase(id="P001", question="test", module="planner")
        results = run_module("planner", [case], live=False)
        assert results[0].status == "skip"
        assert "requires --live" in (results[0].error_msg or "")

    def test_run_module_live_calls_runner(self):
        """live 模式下正确调用 runner。"""
        called = []

        def tracker_runner(cases, **kwargs):
            called.append(len(cases))
            return [
                EvalResult(case_id=c.id, module="planner", status="pass",
                           expected={}, actual={})
                for c in cases
            ]

        register_runner("planner", tracker_runner, needs_live=True)

        cases = [
            TestCase(id="P001", question="q1", module="planner"),
            TestCase(id="P002", question="q2", module="planner"),
        ]
        results = run_module("planner", cases, live=True)
        assert len(results) == 2
        assert called == [2]
        assert all(r.status == "pass" for r in results)

    def test_run_module_passes_kwargs(self):
        """额外 kwargs 正确透传。"""
        received_kwargs = {}

        def kwarg_runner(cases, **kwargs):
            received_kwargs.update(kwargs)
            return [
                EvalResult(case_id=c.id, module="rag", status="pass",
                           expected={}, actual={})
                for c in cases
            ]

        register_runner("rag", kwarg_runner, needs_live=False)

        case = TestCase(id="R001", question="test", module="rag")
        run_module("rag", [case], custom_flag="hello", judge=True)
        assert received_kwargs.get("custom_flag") == "hello"
        assert received_kwargs.get("judge") is True

    def test_runner_exception_returns_error(self):
        """runner 抛异常 → error 状态。"""
        def crashing_runner(cases, **kwargs):
            raise RuntimeError("boom")

        register_runner("rag", crashing_runner, needs_live=False)

        case = TestCase(id="R001", question="test", module="rag")
        results = run_module("rag", [case])
        assert results[0].status == "error"
        assert "boom" in (results[0].error_msg or "")


# ==================== RAG runner（需要项目依赖） ====================

class TestRunRag:
    """RAG 集成测试 — 需要项目 environment 和 ChromaDB。"""

    @pytest.fixture
    def rag_cases(self):
        return load_dataset("rag")[:5]

    def test_returns_results(self, rag_cases):
        from evaluation.runners.builtin import _run_rag
        results = _run_rag(rag_cases)
        assert len(results) == len(rag_cases)
        assert all(isinstance(r, EvalResult) for r in results)

    def test_result_has_expected_fields(self, rag_cases):
        from evaluation.runners.builtin import _run_rag
        results = _run_rag(rag_cases)
        for r in results:
            assert r.case_id
            assert r.module == "rag"
            assert r.status in ("pass", "fail", "error", "skip")
            assert "recall@5" in r.metrics
            assert r.duration_ms >= 0

    def test_empty_cases(self):
        from evaluation.runners.builtin import _run_rag
        results = _run_rag([])
        assert results == []


# ==================== 辅助函数 ====================

class TestSkipAndErrorHelpers:
    def test_skip_results(self):
        cases = [
            TestCase(id="P001", question="q1", module="planner"),
            TestCase(id="P002", question="q2", module="planner"),
        ]
        results = _skip_results(cases, "planner", "not available")
        assert len(results) == 2
        assert all(r.status == "skip" for r in results)
        assert all("not available" in (r.error_msg or "") for r in results)

    def test_error_results(self):
        cases = [TestCase(id="X001", question="q", module="planner")]
        results = _error_results(cases, "planner", "oops")
        assert results[0].status == "error"
        assert "oops" in (results[0].error_msg or "")
