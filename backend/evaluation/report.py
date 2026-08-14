"""报告生成器 — 控制台摘要 + Markdown 详细报告 + 历史对比 + JSON 全量导出。"""

import json
from pathlib import Path
from datetime import datetime
from backend.evaluation.models import EvalReport, ModuleSummary


def print_summary(report: EvalReport) -> None:
    """打印控制台摘要表格。"""
    mode_label = "LIVE" if report.mode == "live" else "OFFLINE"
    header = f"Eval Report — {report.module} ({mode_label})"
    if report.smoke:
        header += " [SMOKE]"
    print(f"\n{'='*60}")
    print(f"  {header}")
    print(f"  {report.timestamp}")
    print(f"{'='*60}")

    for s in report.summaries:
        print(f"\n  [{s.module.upper()}]  pass_rate={s.pass_rate:.1%}  "
              f"({s.passed}/{s.total} passed, {s.failed} failed, {s.errors} errors)")
        if s.metrics:
            for k, v in s.metrics.items():
                print(f"    {k}: {v}")

    if report.total_score is not None:
        print(f"\n  >>> TOTAL SCORE: {report.total_score:.2%} <<<")

    print(f"\n{'='*60}\n")


def write_markdown_report(report: EvalReport, output_dir: Path) -> Path:
    """生成 Markdown 详细报告，保存到 output_dir，返回文件路径。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = output_dir / f"eval-{report.module}-{ts}.md"

    lines = [
        f"# Eval Report — {report.module}",
        f"",
        f"- **Timestamp:** {report.timestamp}",
        f"- **Mode:** {report.mode}",
        f"- **Smoke:** {report.smoke}",
        f"",
    ]

    if report.total_score is not None:
        lines.append(f"## Total Score: {report.total_score:.2%}")
        lines.append("")

    for s in report.summaries:
        lines.append(f"### {s.module.upper()}")
        lines.append(f"")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Total | {s.total} |")
        lines.append(f"| Passed | {s.passed} |")
        lines.append(f"| Failed | {s.failed} |")
        lines.append(f"| Errors | {s.errors} |")
        lines.append(f"| Skipped | {s.skipped} |")
        lines.append(f"| **Pass Rate** | **{s.pass_rate:.1%}** |")
        for k, v in s.metrics.items():
            lines.append(f"| {k} | {v} |")
        lines.append("")

    # 失败/错误详情
    lines.append("## Details")
    lines.append("")
    for r in report.results:
        if r.status in ("fail", "error"):
            lines.append(f"- **{r.case_id}** [{r.status}] — {r.error_msg or 'metrics: ' + str(r.metrics)}")

    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report saved to: {path}")
    return path


def write_json_report(report: EvalReport, output_dir: Path) -> Path:
    """生成 JSON 详细报告（含每条用例的完整检索轨迹），保存到 output_dir，返回文件路径。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = output_dir / f"eval-{report.module}-{ts}.json"

    data = {
        "timestamp": report.timestamp,
        "module": report.module,
        "mode": report.mode,
        "smoke": report.smoke,
        "total_score": report.total_score,
        "summaries": [
            {
                "module": s.module,
                "total": s.total,
                "passed": s.passed,
                "failed": s.failed,
                "errors": s.errors,
                "skipped": s.skipped,
                "pass_rate": s.pass_rate,
                "metrics": s.metrics,
            }
            for s in report.summaries
        ],
        "results": [
            {
                "case_id": r.case_id,
                "module": r.module,
                "status": r.status,
                "expected": r.expected,
                "actual": r.actual,
                "metrics": r.metrics,
                "duration_ms": r.duration_ms,
                "error_msg": r.error_msg,
            }
            for r in report.results
        ],
    }

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON report saved to: {path}")
    return path


def compare_reports(report_a: EvalReport, report_b: EvalReport) -> str:
    """比较两次报告，返回差异描述字符串。"""
    lines = ["## Report Comparison", ""]
    lines.append(f"Base: {report_a.timestamp}  |  Compare: {report_b.timestamp}")
    lines.append("")

    if report_a.total_score and report_b.total_score:
        delta = report_b.total_score - report_a.total_score
        arrow = "↑" if delta > 0 else "↓" if delta < 0 else "→"
        lines.append(f"**Total Score:** {report_a.total_score:.2%} → {report_b.total_score:.2%}  {arrow} {delta:+.2%}")

    lines.append("")
    lines.append("| Module | Base | Compare | Δ |")
    lines.append("|--------|------|---------|---|")
    for sb in report_b.summaries:
        sa = next((s for s in report_a.summaries if s.module == sb.module), None)
        if sa:
            delta = sb.pass_rate - sa.pass_rate
            lines.append(f"| {sb.module} | {sa.pass_rate:.1%} | {sb.pass_rate:.1%} | {delta:+.1%} |")

    result = "\n".join(lines)
    print(result)
    return result


def flag_regressions(
    base: EvalReport, current: EvalReport, threshold: float = 0.05,
) -> list[str]:
    """标记指标下降：当前报告 vs 基线报告，降幅超阈值返回告警列表。

    Args:
        base: 基线报告（历史）
        current: 当前报告
        threshold: 降幅阈值（默认 0.05，即 5%）

    Returns:
        告警字符串列表（空列表表示无显著下降）
    """
    warnings: list[str] = []
    for cb in current.summaries:
        bb = next((s for s in base.summaries if s.module == cb.module), None)
        if bb is None:
            continue
        # 对比每个指标
        for key, cur_val in cb.metrics.items():
            base_val = bb.metrics.get(key)
            if base_val is None:
                continue
            delta = cur_val - base_val
            if delta < -threshold:
                warnings.append(
                    f"⚠️  {cb.module}.{key}: {base_val:.4f} → {cur_val:.4f} "
                    f"(↓{abs(delta):.4f})"
                )
        # 对比 pass_rate
        delta = cb.pass_rate - bb.pass_rate
        if delta < -threshold:
            warnings.append(
                f"⚠️  {cb.module}.pass_rate: {bb.pass_rate:.1%} → "
                f"{cb.pass_rate:.1%} (↓{abs(delta):.1%})"
            )
    return warnings
