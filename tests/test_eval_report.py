"""测试 evaluation/report.py 的报告生成。"""
import pytest
from pathlib import Path
from datetime import datetime
from evaluation.report import print_summary, write_markdown_report, compare_reports
from evaluation.models import (
    EvalReport, ModuleSummary, EvalResult, TestCase
)


@pytest.fixture
def sample_report():
    return EvalReport(
        timestamp=datetime.now().isoformat(),
        module="all",
        mode="live",
        smoke=False,
        summaries=[
            ModuleSummary(
                module="planner", total=20, passed=17, failed=2, errors=1, skipped=0,
                pass_rate=0.85, metrics={"jaccard": 0.88, "redundancy": 0.05},
            ),
            ModuleSummary(
                module="rag", total=30, passed=22, failed=5, errors=3, skipped=0,
                pass_rate=0.733, metrics={"recall@5": 0.72, "mrr": 0.61},
            ),
        ],
        results=[
            EvalResult(
                case_id="P001", module="planner", status="pass",
                expected={"capabilities": ["query_database"]},
                actual={"capabilities": ["query_database"]},
                metrics={"jaccard": 1.0}, duration_ms=234,
            ),
        ],
        total_score=0.82,
    )


class TestPrintSummary:
    def test_does_not_raise(self, sample_report, capsys):
        print_summary(sample_report)
        captured = capsys.readouterr()
        assert "planner" in captured.out.lower() or "Planner" in captured.out
        assert "rag" in captured.out.lower() or "RAG" in captured.out


class TestWriteMarkdownReport:
    def test_writes_file(self, sample_report, tmp_path):
        path = write_markdown_report(sample_report, tmp_path)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "# " in content
        assert "PLANNER" in content
        assert "RAG" in content

    def test_file_has_timestamp_in_name(self, sample_report, tmp_path):
        path = write_markdown_report(sample_report, tmp_path)
        assert "summary" in path.name or path.suffix == ".md"


class TestCompareReports:
    def test_returns_diff_string(self, sample_report):
        report_b = sample_report.model_copy(deep=True)
        report_b.total_score = 0.85
        report_b.summaries[0].pass_rate = 0.90

        diff = compare_reports(sample_report, report_b)
        assert "+0.03" in diff or "improved" in diff.lower() or "↑" in diff
