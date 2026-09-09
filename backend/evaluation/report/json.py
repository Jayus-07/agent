"""JSON 全量报告导出（含 Release Gate 摘要）。"""
import json
from datetime import datetime
from pathlib import Path

from backend.evaluation.models import EvalReport
from .builder import (
    compute_dataset_validation,
    compute_performance_stats,
    compute_release_gate,
)


def write_json_report(
    report: EvalReport,
    output_dir: Path,
    thresholds: dict[str, float] | None = None,
) -> Path:
    """生成 JSON 详细报告（含 Release Gate + 数据集验证），保存到 output_dir，返回文件路径。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = output_dir / f"eval-{report.module}-{ts}.json"

    rag_summary = next((s for s in report.summaries if s.module == "rag"), None)
    metrics = rag_summary.metrics if rag_summary else {}
    gate = compute_release_gate(metrics, thresholds)
    dataset_val = compute_dataset_validation(report)
    perf = compute_performance_stats(report.results)

    data = {
        "timestamp": report.timestamp,
        "module": report.module,
        "mode": report.mode,
        "smoke": report.smoke,
        "tier": report.tier,
        "total_score": report.total_score,
        "release_gate": {
            "verdict": "PASS" if gate["overall"] else "FAIL",
            "metrics": gate["items"],
        },
        "dataset_validation": dataset_val,
        "performance": perf,
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
        "tier_summaries": [
            {
                "tier": t.tier,
                "total": t.total,
                "passed": t.passed,
                "failed": t.failed,
                "pass_rate": t.pass_rate,
                "threshold": t.threshold,
                "passed_threshold": t.passed_threshold,
            }
            for t in report.tier_summaries
        ],
        "prompt_versions": report.prompt_versions,
    }

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON report saved to: {path}")
    return path
