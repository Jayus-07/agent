"""测试 evaluation/models.py 的 Pydantic 模型。"""
import pytest
from evaluation.models import TestCase, EvalResult, EvalReport, ModuleSummary


class TestTestCase:
    def test_minimal_creation(self):
        tc = TestCase(id="P001", question="技术部有多少人？", module="planner")
        assert tc.id == "P001"
        assert tc.expected == {}
        assert tc.metadata == {}

    def test_full_creation(self):
        tc = TestCase(
            id="R001",
            question="冷藏肉类的保质期？",
            module="rag",
            expected={"relevant_docs": ["policy/xxx.txt"], "min_relevant_chunks": 1},
            metadata={"kb_id": "policy", "difficulty": "easy"},
        )
        assert tc.expected["relevant_docs"] == ["policy/xxx.txt"]
        assert tc.metadata["kb_id"] == "policy"

    def test_invalid_module_rejected(self):
        with pytest.raises(Exception):
            TestCase(id="X001", question="test", module="invalid")  # type: ignore


class TestEvalResult:
    def test_pass_result(self):
        r = EvalResult(
            case_id="P001",
            module="planner",
            status="pass",
            expected={"capabilities": ["query_database"]},
            actual={"capabilities": ["query_database"]},
            metrics={"jaccard": 1.0},
        )
        assert r.status == "pass"
        assert r.metrics["jaccard"] == 1.0

    def test_error_result(self):
        r = EvalResult(
            case_id="S099",
            module="sql",
            status="error",
            expected={},
            actual={},
            error_msg="LLM timeout",
        )
        assert r.status == "error"
        assert r.error_msg == "LLM timeout"


class TestEvalReport:
    def test_empty_report(self):
        report = EvalReport(module="rag", mode="offline", summaries=[], results=[])
        assert report.module == "rag"
        assert report.total_score is None

    def test_full_report(self):
        summary = ModuleSummary(
            module="rag", total=30, passed=22, failed=5, errors=3, skipped=0,
            pass_rate=0.733, metrics={"recall@5": 0.72, "mrr": 0.61},
        )
        report = EvalReport(
            module="all", mode="live", smoke=False,
            summaries=[summary], results=[], total_score=0.82,
        )
        assert report.total_score == 0.82
        assert report.summaries[0].pass_rate == 0.733
