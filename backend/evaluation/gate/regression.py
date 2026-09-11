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
            "pass_rate": summary.pass_rate,
            "metrics": summary.metrics,
            "total": summary.total,
            "passed": summary.passed,
            "failed": summary.failed,
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
