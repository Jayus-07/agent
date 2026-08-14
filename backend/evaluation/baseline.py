"""Baseline 管理 — V1.0 重构新增。

功能:
- 持久化: 当前报告 → baseline JSON（按 module + dataset_version 分文件）
- 加载: 历史 baseline 反序列化
- 对比: 当前 vs baseline，输出 delta + 回归告警
- CI 拦截: 阈值超标返回非零退出码

数据流:
    report (EvalReport)
        ↓ promote()
    data/baselines/baseline_{module}_{dataset_version}.json
        ↓ load()
    CI gate: 当前 vs baseline，降幅 > threshold → exit 1
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.evaluation.models import EvalReport

# Windows console encoding fix
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

# Baseline 根目录
BASELINE_ROOT = Path("data/baselines")


def baseline_path(module: str, dataset_version: str) -> Path:
    """生成 baseline 文件路径。"""
    return BASELINE_ROOT / f"baseline_{module}_{dataset_version}.json"


def promote(report: EvalReport, *, git_tag: bool = True) -> list[Path]:
    """将当前报告设为 baseline。

    Args:
        report: 评估报告
        git_tag: 是否自动创建 git tag (eval-baseline-{module}-{date})

    Returns:
        写入的 baseline 文件路径列表
    """
    from backend.evaluation.storage import get_dataset_version
    from backend.evaluation.report import MODULE_LABELS

    BASELINE_ROOT.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    timestamp = datetime.now().strftime("%Y-%m-%d")

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
        }
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(path)
        mod_zh = MODULE_LABELS.get(summary.module, summary.module)
        print(f"[baseline] {mod_zh} 已提升: {path}")

        # Git tag（仅当在 git 仓库中）
        if git_tag:
            _try_git_tag(summary.module, version, timestamp)

    return written


def _try_git_tag(module: str, version: str, date: str) -> None:
    """尝试创建 git tag（失败不抛异常）。"""
    tag_name = f"eval-baseline-{module}-{version}-{date}"
    try:
        subprocess.run(
            ["git", "tag", tag_name],
            check=True, capture_output=True, text=True,
        )
        print(f"[baseline] git tag 已创建: {tag_name}")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"[baseline] git tag 跳过: {e}")


def load(module: str, dataset_version: str) -> dict[str, Any] | None:
    """加载 baseline JSON。无文件时返回 None。"""
    path = baseline_path(module, dataset_version)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def diff(
    report: EvalReport,
    *,
    threshold: float = 0.05,
    critical_metrics: dict[str, dict[str, float]] | None = None,
) -> tuple[list[str], list[str]]:
    """对比当前报告与 baseline，输出 (warnings, errors)。

    Args:
        report: 当前 EvalReport
        threshold: 指标降幅阈值，默认 5%
        critical_metrics: 模块特定的关键指标及阈值，如
            {"rag": {"recall@10": 0.10}} — 超过该阈值视为 critical failure

    Returns:
        (warnings, errors):
            warnings: pass_rate 或指标降幅 5~critical 的告警
            errors: 降幅超 critical 阈值的失败
    """
    from backend.evaluation.storage import get_dataset_version

    warnings: list[str] = []
    errors: list[str] = []
    critical_metrics = critical_metrics or {}

    # V1.0: 中文化标签
    from backend.evaluation.report import METRIC_LABELS, MODULE_LABELS

    for summary in report.summaries:
        version = get_dataset_version(summary.module)
        base = load(summary.module, version)
        if base is None:
            mod_zh = MODULE_LABELS.get(summary.module, summary.module)
            warnings.append(
                f"⚠️  {mod_zh}: 无 baseline（dataset_version={version}），跳过回归检查"
            )
            continue

        mod_zh = MODULE_LABELS.get(summary.module, summary.module)

        # 1. pass_rate 检查（pass_rate 降幅 > threshold 即视为 critical error，
        #   因为 pass_rate 是综合质量的最直接信号）
        delta = summary.pass_rate - base["pass_rate"]
        if delta < -threshold:
            errors.append(
                f"❌ {mod_zh}.通过率: {base['pass_rate']:.2%} → "
                f"{summary.pass_rate:.2%} (↓{abs(delta):.2%})"
            )

        # 2. 逐指标检查（warning: >threshold；error: >2*threshold 或 critical_metrics 配置）
        crit = critical_metrics.get(summary.module, {})
        for key, cur_val in summary.metrics.items():
            base_val = base["metrics"].get(key)
            if base_val is None:
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

    return warnings, errors


def check_regression(
    report: EvalReport,
    *,
    threshold: float = 0.05,
    critical_metrics: dict[str, dict[str, float]] | None = None,
) -> int:
    """CI 拦截入口 — 返回 shell exit code。

    Returns:
        0 = 通过；1 = 有 warning 但可放行；2 = 有 error 阻断
    """
    warnings, errors = diff(report, threshold=threshold, critical_metrics=critical_metrics)

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


def cli_main() -> int:
    """CLI 入口: python -m evaluation.baseline_check <run_id>"""
    import argparse
    from backend.evaluation.storage import load_report

    parser = argparse.ArgumentParser(
        prog="python -m evaluation.baseline_check",
        description="对比指定 run 的报告与 baseline，返回 exit code",
    )
    parser.add_argument("run_id", help="评估 run_id (如 2026-08-14T10-00-00)")
    parser.add_argument("--threshold", type=float, default=0.05)
    args = parser.parse_args()

    report, _ = load_report(args.run_id)
    return check_regression(report, threshold=args.threshold)


if __name__ == "__main__":
    import sys
    sys.exit(cli_main())
