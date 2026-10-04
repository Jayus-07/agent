"""回归检测 + 基线管理。"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.evaluation.models import EvalReport

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


def flag_regressions(
    base: EvalReport,
    current: EvalReport,
    threshold: float = 0.05,
) -> list[str]:
    """标记指标下降：当前报告 vs 基线报告，降幅超阈值返回告警列表（中文）。"""
    from backend.evaluation.report import METRIC_LABELS, MODULE_LABELS

    warnings: list[str] = []
    for cb in current.summaries:
        bb = next((s for s in base.summaries if s.module == cb.module), None)
        if bb is None:
            continue
        mod_zh = MODULE_LABELS.get(cb.module, cb.module)
        for key, cur_val in cb.metrics.items():
            # metrics 可能含嵌套 dict（token_summary）/None（无法计算），跳过
            if isinstance(cur_val, bool) or not isinstance(cur_val, (int, float)):
                continue
            base_val = bb.metrics.get(key)
            if isinstance(base_val, bool) or not isinstance(base_val, (int, float)):
                continue
            delta = cur_val - base_val
            if delta < -threshold:
                label = METRIC_LABELS.get(key, key)
                warnings.append(
                    f"⚠️  {mod_zh}.{label}: {base_val:.4f} → {cur_val:.4f} "
                    f"(↓{abs(delta):.4f})"
                )
        delta = cb.pass_rate - bb.pass_rate
        if delta < -threshold:
            warnings.append(
                f"⚠️  {mod_zh}.通过率: {bb.pass_rate:.1%} → "
                f"{cb.pass_rate:.1%} (↓{abs(delta):.1%})"
            )
    return warnings


# ==================== 基线管理 ====================

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
BASELINE_ROOT = _PROJECT_ROOT / "data" / "baselines"


def baseline_path(module: str, dataset_version: str) -> Path:
    return BASELINE_ROOT / f"baseline_{module}_{dataset_version}.json"


def promote(report: EvalReport, *, git_tag: bool = True) -> list[Path]:
    """将当前报告设为 baseline。"""
    from backend.evaluation.report import MODULE_LABELS
    from backend.evaluation.storage import get_dataset_version

    BASELINE_ROOT.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    timestamp = datetime.now().strftime("%Y-%m-%d")

    try:
        from backend.prompts.service import snapshot_prompt_versions
        prompt_versions = snapshot_prompt_versions()
    except Exception:
        prompt_versions = {}

    for summary in report.summaries:
        version = get_dataset_version(summary.module)
        path = baseline_path(summary.module, version)
        data = {
            "module": summary.module,
            "dataset_version": version,
            "promoted_at": timestamp,
            # 源 run 记录（2026-10-04 起）：逐样本回归（C1-3）与分桶回归
            # （C1-5）需要基线报告本体，经 run_id 从 data/eval_runs 加载；
            # 存量无此字段的 baseline 走「仅摘要级对比」降级，不伪造样本清单。
            "run_id": str(report.metadata.get("run_id") or ""),
            "pass_rate": summary.pass_rate,
            "metrics": summary.metrics,
            "total": summary.total,
            "passed": summary.passed,
            "failed": summary.failed,
            "buckets": report.metadata.get("buckets") or {},
            "prompt_versions": report.prompt_versions or prompt_versions,
        }
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(path)
        mod_zh = MODULE_LABELS.get(summary.module, summary.module)
        print(f"[baseline] {mod_zh} 已提升: {path}")

        if git_tag:
            _try_git_tag(summary.module, version, timestamp)

    return written


def _try_git_tag(module: str, version: str, date: str) -> None:
    tag_name = f"eval-baseline-{module}-{version}-{date}"
    try:
        subprocess.run(
            ["git", "tag", tag_name],
            check=True, capture_output=True, text=True,
        )
        print(f"[baseline] git tag 已创建: {tag_name}")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"[baseline] git tag 跳过: {e}")


def load_baseline(module: str, dataset_version: str) -> dict[str, Any] | None:
    path = baseline_path(module, dataset_version)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _num(value: Any) -> float | None:
    """数值提取：bool/非数值一律视为不可比（None）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def evaluate_regression_gate(
    module: str,
    dataset_version: str,
    current_metrics: dict[str, Any],
    current_pass_rate: float,
    *,
    threshold: float = 0.05,
    critical_metrics: dict[str, dict[str, float]] | None = None,
    current_buckets: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """结构化回归门结果（C1-1/GATE-11）— 供 Release Gate 与 CLI 消费。

    与 ``diff_baseline``（面向 CLI 打印）不同，本函数输出 JSON 可序列化的
    结构化判定，且对「无 baseline」给出显式 ``baseline_unavailable`` 口径
    （REG-10：不伪造 delta=0，也不误判为失败）。

    Returns:
        {
          "baseline_available": bool,
          "baseline_run_id": str,            # 基线 JSON 记录的源 run（新版才有）
          "baseline_dataset_version": str,
          "regression_pass": bool | None,    # None=无 baseline 不可判
          "errors":  [{metric, baseline, current, delta, threshold, severity}],
          "warnings": [同上],
          "bucket_regressions": [...],       # C1-5 分桶回归
          "messages": [中文人类可读明细],
        }
    """
    base = load_baseline(module, dataset_version)
    if base is None:
        return {
            "baseline_available": False,
            "baseline_run_id": "",
            "baseline_dataset_version": dataset_version,
            "regression_pass": None,
            "errors": [],
            "warnings": [],
            "bucket_regressions": [],
            "messages": [
                f"baseline_unavailable: 无 baseline（module={module}, "
                f"dataset_version={dataset_version}），回归门跳过并留痕"
            ],
        }

    crit = (critical_metrics or {}).get(module) or (critical_metrics or {}).get("*", {})
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    messages: list[str] = []

    def _record(kind: list, metric: str, base_val: float, cur_val: float,
                allowed: float, severity: str) -> None:
        delta = cur_val - base_val
        entry = {
            "metric": metric,
            "baseline": round(base_val, 4),
            "current": round(cur_val, 4),
            "delta": round(delta, 4),
            "threshold": allowed,
            "severity": severity,
        }
        kind.append(entry)
        messages.append(
            f"{metric}: {base_val:.4f} → {cur_val:.4f} (↓{abs(delta):.4f})"
        )

    base_pass = _num(base.get("pass_rate"))
    cur_pass = _num(current_pass_rate)
    if base_pass is not None and cur_pass is not None:
        delta = cur_pass - base_pass
        if delta < -threshold:
            _record(errors, "pass_rate", base_pass, cur_pass, threshold, "error")

    base_metrics = base.get("metrics") or {}
    for key, cur_raw in (current_metrics or {}).items():
        cur_val = _num(cur_raw)
        if cur_val is None:
            continue
        base_val = _num(base_metrics.get(key))
        if base_val is None:
            continue
        delta = cur_val - base_val
        allowed = crit.get(key, threshold)
        if delta < -allowed:
            severity = "error" if delta < -2 * allowed else "warning"
            _record(errors if severity == "error" else warnings,
                    key, base_val, cur_val, allowed, severity)

    bucket_regressions = compare_buckets(
        current_buckets or {}, base.get("buckets") or {},
    )

    regression_pass = not errors and not bucket_regressions
    return {
        "baseline_available": True,
        "baseline_run_id": str(base.get("run_id", "") or ""),
        "baseline_dataset_version": str(base.get("dataset_version", "")),
        "regression_pass": regression_pass,
        "errors": errors,
        "warnings": warnings,
        "bucket_regressions": bucket_regressions,
        "messages": messages,
    }


def compare_buckets(
    current: dict[str, Any],
    baseline: dict[str, Any],
    *,
    threshold: float = 0.10,
    min_bucket_samples: int = 3,
) -> list[dict[str, Any]]:
    """C1-5/REG-06：分桶回归检测（by_domain/by_difficulty/by_query_type）。

    overall 达标但单桶大幅下降是典型回归盲区。任一桶 pass_rate 下降超过
    ``threshold`` 且样本数足够（小桶噪声大，不误报）→ 记一条回归。
    """
    regressions: list[dict[str, Any]] = []
    for dim, base_slots in (baseline or {}).items():
        cur_slots = (current or {}).get(dim) or {}
        for bucket, base_slot in (base_slots or {}).items():
            cur_slot = cur_slots.get(bucket)
            if not cur_slot:
                continue
            base_n = int(base_slot.get("total", 0))
            cur_n = int(cur_slot.get("total", 0))
            if base_n < min_bucket_samples or cur_n < min_bucket_samples:
                continue
            base_rate = _num(base_slot.get("pass_rate"))
            cur_rate = _num(cur_slot.get("pass_rate"))
            if base_rate is None or cur_rate is None:
                continue
            delta = cur_rate - base_rate
            if delta < -threshold:
                regressions.append({
                    "dimension": dim,
                    "bucket": bucket,
                    "baseline_pass_rate": round(base_rate, 4),
                    "current_pass_rate": round(cur_rate, 4),
                    "delta": round(delta, 4),
                    "threshold": threshold,
                    "severity": "error",
                    "sample_counts": {"baseline": base_n, "current": cur_n},
                })
    return regressions


def diff_samples(
    current_report: EvalReport,
    baseline_report: EvalReport,
) -> dict[str, Any]:
    """C1-3/REG-03/04：逐样本回归/提升清单（按 case_id 对比 status）。

    Returns:
        {
          "regression_samples":  [{case_id, baseline_status, current_status}],  # pass→fail
          "improvement_samples": [{case_id, baseline_status, current_status}],  # fail/error→pass
          "compared": int,                   # 两边都出现的 case 数
          "note": str,                       # 不可比时的显式说明
        }
    """
    baseline_status = {r.case_id: r.status for r in baseline_report.results}
    current_status = {r.case_id: r.status for r in current_report.results}
    compared_ids = set(baseline_status) & set(current_status)

    regression_samples = [
        {"case_id": cid, "baseline_status": baseline_status[cid],
         "current_status": current_status[cid]}
        for cid in sorted(compared_ids)
        if baseline_status[cid] == "pass" and current_status[cid] != "pass"
    ]
    improvement_samples = [
        {"case_id": cid, "baseline_status": baseline_status[cid],
         "current_status": current_status[cid]}
        for cid in sorted(compared_ids)
        if baseline_status[cid] != "pass" and current_status[cid] == "pass"
    ]
    note = ""
    if not compared_ids:
        note = "baseline_unavailable: 两份报告无共同 case_id，逐样本对比不可判"
    return {
        "regression_samples": regression_samples,
        "improvement_samples": improvement_samples,
        "compared": len(compared_ids),
        "note": note,
    }


def load_baseline_report(module: str, dataset_version: str) -> EvalReport | None:
    """按 baseline JSON 记录的 run_id 加载基线报告（逐样本/分桶对比用）。

    存量 baseline JSON 无 run_id 字段 → 返回 None（调用方显式降级，
    不得伪造空清单冒充「无回归」）。
    """
    base = load_baseline(module, dataset_version)
    if not base or not base.get("run_id"):
        return None
    try:
        from backend.evaluation.storage import load_report

        report, _meta = load_report(str(base["run_id"]))
        return report
    except (FileNotFoundError, ValueError, OSError):
        return None


def diff_baseline(
    report: EvalReport,
    *,
    threshold: float = 0.05,
    critical_metrics: dict[str, dict[str, float]] | None = None,
) -> tuple[list[str], list[str]]:
    """对比当前报告与 baseline，输出 (warnings, errors)。"""
    from backend.evaluation.report import METRIC_LABELS, MODULE_LABELS
    from backend.evaluation.storage import get_dataset_version

    warnings: list[str] = []
    errors: list[str] = []
    critical_metrics = critical_metrics or {}

    for summary in report.summaries:
        version = get_dataset_version(summary.module)
        base = load_baseline(summary.module, version)
        if base is None:
            mod_zh = MODULE_LABELS.get(summary.module, summary.module)
            warnings.append(
                f"⚠️  {mod_zh}: 无 baseline（dataset_version={version}），跳过回归检查"
            )
            continue

        mod_zh = MODULE_LABELS.get(summary.module, summary.module)

        delta = summary.pass_rate - base["pass_rate"]
        if delta < -threshold:
            errors.append(
                f"❌ {mod_zh}.通过率: {base['pass_rate']:.2%} → "
                f"{summary.pass_rate:.2%} (↓{abs(delta):.2%})"
            )

        crit = critical_metrics.get(summary.module) or critical_metrics.get("*", {})
        for key, cur_val in summary.metrics.items():
            # metrics 可能含嵌套 dict（token_summary）/None（无法计算），跳过
            if isinstance(cur_val, bool) or not isinstance(cur_val, (int, float)):
                continue
            base_val = base["metrics"].get(key)
            if isinstance(base_val, bool) or not isinstance(base_val, (int, float)):
                continue
            delta = cur_val - base_val
            crit_threshold = crit.get(key, threshold)
            if delta < -crit_threshold:
                severity = "error" if delta < -2 * crit_threshold else "warning"
                label = METRIC_LABELS.get(key, key)
                msg = (
                    f"{'❌' if severity == 'error' else '⚠️ '} "
                    f"{mod_zh}.{label}: {base_val:.4f} → "
                    f"{cur_val:.4f} (↓{abs(delta):.4f})"
                )
                (errors if severity == "error" else warnings).append(msg)

    if errors and report.prompt_versions:
        try:
            from backend.evaluation.storage import get_dataset_version
            for summary in report.summaries:
                base = load_baseline(summary.module, get_dataset_version(summary.module))
                if not base or "prompt_versions" not in base:
                    continue
                base_pv = base["prompt_versions"]
                changed = [
                    f"{k}: v{base_pv[k]}→v{report.prompt_versions.get(k)}"
                    for k in base_pv
                    if report.prompt_versions.get(k) != base_pv.get(k)
                ]
                if changed:
                    mod_zh = MODULE_LABELS.get(summary.module, summary.module)
                    warnings.append(
                        f"ℹ️  {mod_zh}: 以下 prompt 版本已变更 — {', '.join(changed)}"
                    )
        except Exception:
            pass

    return warnings, errors


def check_regression(
    report: EvalReport,
    *,
    threshold: float = 0.05,
    critical_metrics: dict[str, dict[str, float]] | None = None,
) -> int:
    """CI 拦截入口 — 返回 shell exit code。

    Returns:
        0 = 通过（无 error，warnings 仅打印不阻断）
        2 = 阻断（有 error，CI 将失败）
    """
    # critical_metrics 未显式配置时的默认分级阈值（key "*" 对所有模块生效）：
    # 通过率等核心指标对噪声更敏感，统一 5% 绝对阈值会让 4.9% 的下降漏告警
    effective_critical = critical_metrics if critical_metrics is not None else {
        "*": {
            "pass_rate": 0.02,
            "recall@5": 0.03,
            "recall@10": 0.03,
            "sem_faithfulness": 0.03,
            "S7_faithfulness": 0.03,
        },
    }
    warnings, errors = diff_baseline(
        report, threshold=threshold, critical_metrics=effective_critical,
    )

    print(f"\n{'='*60}")
    print(f"  基线回归检查 (阈值={threshold:.0%})")
    print(f"{'='*60}")

    if warnings:
        print(f"\n⚠️  警告 ({len(warnings)} 项):")
        for w in warnings:
            print(f"  {w}")

    if errors:
        print(f"\n❌ 错误 ({len(errors)} 项) — CI 将失败:")
        for e in errors:
            print(f"  {e}")
        print(f"{'='*60}\n")
        return 2

    if not warnings:
        print("\n✅ 未检测到回归 — 所有指标在阈值范围内。")
    print(f"{'='*60}\n")
    return 0
