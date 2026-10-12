"""报告时间在各输出中统一显示上海时区。"""
import json
from datetime import datetime, timedelta

from backend.evaluation.models import EvalReport, EvalResult, ModuleSummary
from backend.evaluation.report.builder import print_summary
from backend.evaluation.report.json import write_json_report
from backend.evaluation.report.markdown import write_markdown_report
from backend.evaluation.report.timestamp import format_shanghai_timestamp


_EXPECTED_SHANGHAI_TIME = "2026 年 10 月 13 日 01:14:44（上海时间）"


def _make_report(timestamp: str | None = None) -> EvalReport:
    summary = ModuleSummary(
        module="rag",
        total=1,
        passed=1,
        failed=0,
        errors=0,
        skipped=0,
        pass_rate=1.0,
    )
    result = EvalResult(
        case_id="R001",
        module="rag",
        status="pass",
        expected={},
        actual={},
    )
    report_data = {
        "module": "rag",
        "mode": "offline",
        "summaries": [summary],
        "results": [result],
    }
    if timestamp is not None:
        report_data["timestamp"] = timestamp
    return EvalReport(**report_data)


def test_format_shanghai_timestamp_converts_utc_and_legacy_naive_values():
    assert format_shanghai_timestamp("2026-10-12T17:14:44.522322") == (
        _EXPECTED_SHANGHAI_TIME
    )
    assert format_shanghai_timestamp("2026-10-12T17:14:44.522322+00:00") == (
        _EXPECTED_SHANGHAI_TIME
    )
    assert format_shanghai_timestamp("2026-10-12T17:14:44.522322Z") == (
        _EXPECTED_SHANGHAI_TIME
    )


def test_eval_report_defaults_to_timezone_aware_utc():
    report = _make_report()
    parsed = datetime.fromisoformat(report.timestamp)
    assert parsed.utcoffset() == timedelta(0)


def test_report_outputs_include_shanghai_time(tmp_path, capsys):
    report = _make_report("2026-10-12T17:14:44.522322+00:00")

    markdown_path = write_markdown_report(report, tmp_path / "markdown")
    markdown = markdown_path.read_text(encoding="utf-8")
    assert f"- **时间（上海）**: {_EXPECTED_SHANGHAI_TIME}" in markdown
    assert f'"timestamp_shanghai": "{_EXPECTED_SHANGHAI_TIME}"' in markdown

    json_path = write_json_report(report, tmp_path / "json")
    json_report = json.loads(json_path.read_text(encoding="utf-8"))
    assert json_report["timestamp"] == report.timestamp
    assert json_report["timestamp_shanghai"] == _EXPECTED_SHANGHAI_TIME

    print_summary(report)
    assert f"时间（上海）: {_EXPECTED_SHANGHAI_TIME}" in capsys.readouterr().out
