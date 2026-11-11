"""CLI 历史 RAGAS 对比回归测试。"""
from __future__ import annotations

from backend.evaluation.cli import _do_compare
from backend.evaluation.models import EvalReport, EvalResult, ModuleSummary


def _report(timestamp: str, ragas_score: float) -> EvalReport:
    summary = ModuleSummary(
        module="rag", total=1, passed=1, failed=0, errors=0, skipped=0,
        pass_rate=1.0,
        metrics={
            "recall@5": 0.9, "mrr": 0.9, "reject_accuracy": 1.0,
            "false_answer_rate": 0.0, "ragas_faithfulness": ragas_score,
        },
    )
    result = EvalResult(
        case_id="R1", module="rag", status="pass", expected={}, actual={},
    )
    return EvalReport(
        timestamp=timestamp, module="rag", mode="live", summaries=[summary],
        results=[result],
    )


def test_compare_reports_ragas_variance_without_name_error(tmp_path, capsys):
    previous = _report("2026-11-10T00:00:00+00:00", 0.2)
    current = _report("2026-11-11T00:00:00+00:00", 0.9)
    (tmp_path / "report.json").write_text(
        previous.model_dump_json(), encoding="utf-8",
    )

    _do_compare("latest", current, tmp_path)

    assert "RAGAS 最大波动" in capsys.readouterr().out
