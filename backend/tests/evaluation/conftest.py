"""评测测试 conftest — 自动生成 JSON + HTML 报告到 reports/ 目录。

pytest_sessionfinish hook 在测试结束后：
  1. 从 session.config._golden_eval_results 获取 EvalResult 列表
  2. 构建 EvalReport 聚合对象
  3. 调用 write_json_report / write_html_report 输出到 reports/
"""

from pathlib import Path


def _build_eval_report(results):
    """从 list[EvalResult] 构建 EvalReport。"""
    from backend.evaluation.models import EvalReport, ModuleSummary

    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    errors = sum(1 for r in results if r.status == "error")
    skipped = sum(1 for r in results if r.status == "skip")
    total = len(results)

    all_metric_keys: set[str] = set()
    for r in results:
        all_metric_keys.update(r.metrics.keys())

    agg_metrics: dict[str, float] = {}
    for key in all_metric_keys:
        vals = [r.metrics[key] for r in results if key in r.metrics]
        if vals:
            agg_metrics[key] = sum(vals) / len(vals)

    summary = ModuleSummary(
        module="rag",
        total=total,
        passed=passed,
        failed=failed,
        errors=errors,
        skipped=skipped,
        pass_rate=passed / total if total else 0.0,
        metrics=agg_metrics,
    )

    return EvalReport(
        module="rag",
        mode="offline",
        smoke=False,
        summaries=[summary],
        results=results,
        total_score=summary.pass_rate,
    )


def pytest_sessionfinish(session, exitstatus):
    """测试结束后生成 JSON + HTML 报告。"""
    results = getattr(session.config, "_golden_eval_results", None)
    if not results:
        return

    try:
        from backend.evaluation.report import (
            print_summary,
            write_html_report,
            write_json_report,
        )
    except ImportError:
        return

    report = _build_eval_report(results)
    print_summary(report)

    reports_dir = Path(session.config.rootpath) / "reports"
    json_path = write_json_report(report, reports_dir)
    html_path = write_html_report(report, reports_dir)
    print("\n📄 报告已生成:")
    print(f"   JSON: {json_path}")
    print(f"   HTML: {html_path}")
