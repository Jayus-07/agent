"""Prompt 发布门禁使用的本地评测适配器。

2026-10-04 验收收敛（一期）：评测结果之外装配结构化发布门禁判定——
tier 阈值门（SELF-07）+ 最低有效样本量门（GATE-12/DATA-09）+ 基线回归门
（GATE-11，baseline_unavailable 显式口径 REG-10）+ RAGAS 门（GATE-03），
未过规则汇总为 ``blocked_rules`` 数组（GATE-16）随 metrics 落 release 行，
publish 侧按灰度开关（audit/enforce）裁决。
"""
from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from typing import Any

from backend.config.settings import (
    PROMPT_RELEASE_RAGAS_GATE_ENABLED,
    PROMPT_RELEASE_REGRESSION_GATE_ENABLED,
)
from backend.evaluation.config import EvalConfig
from backend.evaluation.service import EvaluationService
from backend.evaluation.storage import persist_report
from backend.infra.llm.registry_store import refresh_registry
from backend.prompts.release_models import PromptReleaseRecord

# GATE-03：RAGAS 建议阈值（清单三：最终阈值必须保存到评测快照，
# 不能只存在前端代码中——随 gate 判定进 release metrics JSONB）
RAGAS_GATE_THRESHOLDS: dict[str, float] = {
    "ragas_faithfulness": 0.85,
    "ragas_answer_relevancy": 0.80,
    "ragas_context_precision": 0.80,
    "ragas_context_recall": 0.85,
}
RAGAS_VALID_RATIO_MIN = 0.90  # C4-7：有效样本率 < 90% → ragas_degraded


async def run_prompt_release_evaluation(
    release: PromptReleaseRecord,
) -> dict[str, Any]:
    """用数据库绑定的外部模型执行发布评测并返回门禁结果。"""
    return await asyncio.to_thread(_run_sync, release)


def _run_sync(release: PromptReleaseRecord) -> dict[str, Any]:
    with _evaluation_env(release.created_by):
        asyncio.run(refresh_registry())
        report = EvaluationService().evaluate(
            EvalConfig(
                module="rag",
                live=True,
                selection=release.eval_suite,
                dataset_version=str(
                    release.dataset_provenance.get("version")
                    or release.dataset_provenance.get("dataset_version")
                    or ""
                ),
                prompt_versions=release.prompt_snapshot,
                release_id=release.release_id,
            )
        )
        run_dir = persist_report(report)

    summaries = [summary for summary in report.summaries if summary.module == "rag"]
    summary = summaries[0] if summaries else None
    gate = _assemble_release_gate(report, summary, release)
    metrics = dict(summary.metrics if summary else {})
    if summary is not None:
        metrics["pass_rate"] = summary.pass_rate
        metrics["total"] = summary.total
        metrics["passed"] = summary.passed
    metrics["gate"] = gate

    blocked = gate["blocked_rules"]
    passed = gate["tier_pass"] and gate["sample_pass"]
    failure_reason = ""
    if not passed:
        failure_reason = "；".join(
            [r.get("message", "") for r in blocked if r.get("severity") == "error"]
        ) or "评测层级未达到阈值"
    return {
        "status": "passed" if passed else "failed",
        "run_id": run_dir.name,
        "metrics": metrics,
        "failure_reason": failure_reason,
        "provenance": {"blocked_rules": blocked},
    }


def _assemble_release_gate(
    report: Any,
    summary: Any,
    release: PromptReleaseRecord,
) -> dict[str, Any]:
    """装配结构化发布门禁判定（GATE-11/12/03/16）。

    记录侧（release 行 metrics）只判 tier + 样本量两道硬门；回归门与
    RAGAS 门在此计算并落痕，发布侧按灰度开关裁决（audit 先行）。
    """
    tier_pass = bool(report.tier_summaries) and all(
        item.passed_threshold for item in report.tier_summaries
    )
    blocked_rules: list[dict[str, Any]] = []
    if not tier_pass:
        for item in report.tier_summaries:
            if not item.passed_threshold:
                blocked_rules.append({
                    "rule": "tier_threshold",
                    "expected": f">= {item.threshold:.2f}",
                    "actual": f"{item.pass_rate:.4f}",
                    "severity": "error",
                    "message": f"[{item.tier}] {item.gate_reasons[0] if item.gate_reasons else '通过率未达阈值'}",
                })

    # ── 最低有效样本量门（GATE-12/DATA-08/09）──
    total = summary.total if summary else 0
    errors = summary.errors if summary else 0
    skipped = summary.skipped if summary else 0
    valid = total - errors - skipped
    governance = report.metadata.get("suite_governance") or {}
    required = (
        governance.get("min_valid_samples")
        or governance.get("min_samples")
        or int(os.getenv("EVAL_MIN_SAMPLES", "8"))
    )
    sample_pass = valid >= required
    if not sample_pass:
        blocked_rules.append({
            "rule": "min_samples",
            "expected": f">= {required}",
            "actual": str(valid),
            "severity": "error",
            "message": (
                f"有效样本不足：valid={valid}（total={total} - errors={errors} "
                f"- skipped={skipped}）< 最低要求 {required}；"
                f"极小样本 100% 通过不构成发布依据"
            ),
        })

    # ── 基线回归门（GATE-11/REG-03/04/06/10）──
    dataset_version = str(
        report.metadata.get("dataset_version")
        or release.dataset_provenance.get("version")
        or release.dataset_provenance.get("dataset_version")
        or ""
    )
    regression = _evaluate_regression(report, summary, dataset_version)
    for entry in regression.get("errors", []):
        blocked_rules.append({
            "rule": "baseline_regression",
            "expected": f"{entry['metric']} 降幅 ≤ {entry['threshold']}",
            "actual": f"{entry['baseline']} → {entry['current']}",
            "severity": "error",
            "message": f"基线回归：{entry['metric']} {entry['baseline']} → {entry['current']}",
        })
    for bucket in regression.get("bucket_regressions", []):
        blocked_rules.append({
            "rule": "bucket_regression",
            "expected": f"{bucket['bucket']} 通过率降幅 ≤ {bucket['threshold']:.0%}",
            "actual": (
                f"{bucket['baseline_pass_rate']} → {bucket['current_pass_rate']}"
            ),
            "severity": "error",
            "message": (
                f"分桶回归：[{bucket['dimension']}/{bucket['bucket']}] "
                f"{bucket['baseline_pass_rate']} → {bucket['current_pass_rate']}"
            ),
        })

    # ── RAGAS 门（GATE-03 双门禁）──
    ragas_gate = _evaluate_ragas_gate(report, summary)
    if (
        PROMPT_RELEASE_RAGAS_GATE_ENABLED == "enforce"
        and not ragas_gate["ragas_pass"]
    ):
        blocked_rules.append({
            "rule": ragas_gate["rule"],
            "expected": ragas_gate["expected"],
            "actual": ragas_gate["actual"],
            "severity": "error",
            "message": ragas_gate["message"],
        })

    return {
        "tier_pass": tier_pass,
        "sample_pass": sample_pass,
        "sample_gate": {"required": required, "valid": valid, "total": total},
        "regression_gate_mode": PROMPT_RELEASE_REGRESSION_GATE_ENABLED,
        "ragas_gate_mode": PROMPT_RELEASE_RAGAS_GATE_ENABLED,
        "regression": regression,
        "ragas": ragas_gate,
        "blocked_rules": blocked_rules,
    }


def _evaluate_regression(
    report: Any, summary: Any, dataset_version: str,
) -> dict[str, Any]:
    """结构化基线回归门；逐样本/分桶维度尽力而为、缺失显式降级。"""
    from backend.evaluation.gate.regression import (
        diff_samples,
        evaluate_regression_gate,
        load_baseline_report,
    )

    result: dict[str, Any] = evaluate_regression_gate(
        "rag",
        dataset_version,
        dict(summary.metrics if summary else {}),
        summary.pass_rate if summary else 0.0,
        current_buckets=report.metadata.get("buckets") or {},
    )
    # 逐样本对比：baseline JSON 带 run_id 且报告仍存在时才可判（REG-03/04）
    baseline_report = load_baseline_report("rag", dataset_version)
    if baseline_report is not None:
        result["samples"] = diff_samples(report, baseline_report)
    else:
        result["samples"] = {
            "regression_samples": [],
            "improvement_samples": [],
            "compared": 0,
            "note": (
                "baseline_samples_unavailable: 基线未记录源 run 或报告已清理，"
                "逐样本对比降级为仅摘要级"
            ),
        }
    return result


def _evaluate_ragas_gate(report: Any, summary: Any) -> dict[str, Any]:
    """RAGAS 门判定：未执行/不可用/有效样本率不足/指标不达标均不通过。"""
    metrics = dict(summary.metrics if summary else {})
    ragas_stats = report.metadata.get("ragas_samples") or {}
    valid = int(ragas_stats.get("valid", 0) or 0)
    invalid = int(ragas_stats.get("invalid", 0) or 0)

    executed = any(key in metrics for key in RAGAS_GATE_THRESHOLDS)
    if not executed:
        return {
            "ragas_pass": False,
            "rule": "ragas_not_executed",
            "expected": "RAGAS 四指标实分且达标（双门禁）",
            "actual": "RAGAS 未执行或未产出任何分值",
            "message": "RAGAS 门：未执行/不可用（fail-closed，不视为通过）",
            "thresholds": dict(RAGAS_GATE_THRESHOLDS),
            "valid_samples": valid,
            "invalid_samples": invalid,
        }

    failed_metrics = []
    for key, threshold in RAGAS_GATE_THRESHOLDS.items():
        value = metrics.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            failed_metrics.append(f"{key}=unavailable(<{threshold})")
        elif value < threshold:
            failed_metrics.append(f"{key}={value:.4f}(<{threshold})")
    attempted = valid + invalid
    valid_ratio = (valid / attempted) if attempted else 0.0
    degraded = attempted > 0 and valid_ratio < RAGAS_VALID_RATIO_MIN
    passed = not failed_metrics and not degraded
    if degraded:
        failed_metrics.append(
            f"valid_ratio={valid_ratio:.2f}(<{RAGAS_VALID_RATIO_MIN}) ragas_degraded"
        )
    return {
        "ragas_pass": passed,
        "rule": "ragas_gate",
        "expected": " / ".join(
            f"{k.removeprefix('ragas_')}>= {v}" for k, v in RAGAS_GATE_THRESHOLDS.items()
        ) + f"（valid_ratio>= {RAGAS_VALID_RATIO_MIN}）",
        "actual": "；".join(failed_metrics) or "全部达标",
        "message": (
            "RAGAS 门：通过" if passed
            else f"RAGAS 门未通过：{'；'.join(failed_metrics)}"
        ),
        "thresholds": dict(RAGAS_GATE_THRESHOLDS),
        "valid_samples": valid,
        "invalid_samples": invalid,
        "valid_ratio": round(valid_ratio, 4),
        "degraded": degraded,
    }


@contextmanager
def _evaluation_env(triggered_by: str):
    old_trigger = os.environ.get("EVAL_TRIGGER")
    old_triggered_by = os.environ.get("EVAL_TRIGGERED_BY")
    os.environ["EVAL_TRIGGER"] = "prompt_publish"
    os.environ["EVAL_TRIGGERED_BY"] = triggered_by or "prompt-publish"
    try:
        yield
    finally:
        _restore_env("EVAL_TRIGGER", old_trigger)
        _restore_env("EVAL_TRIGGERED_BY", old_triggered_by)


def _restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


__all__ = ["run_prompt_release_evaluation"]
