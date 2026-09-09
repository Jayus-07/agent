"""CI 脚本 — 读取最新 JSON 评测报告，提取核心指标，评论到 PR。

用法（CI 中）：
    python backend/scripts/pr_comment_metrics.py

环境变量：
    GH_TOKEN: GitHub token（CI 自动注入 secrets.GITHUB_TOKEN）
    EVAL_RUNS_DIR: 报告目录路径（默认 data/eval_runs/）
"""

import json
import os
import subprocess
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
EVAL_RUNS_DIR = Path(os.environ.get("EVAL_RUNS_DIR", _PROJECT_ROOT / "data" / "eval_runs"))

METRIC_DISPLAY = {
    "sem_context_recall": "语义召回",
    "sem_faithfulness": "忠实度",
    "sem_answer_correctness": "答案正确性",
    "reject_accuracy": "拒答准确率",
    "top1_accuracy": "Top-1 准确率",
    "mrr": "MRR",
}

METRIC_ORDER = [
    "sem_context_recall",
    "sem_faithfulness",
    "sem_answer_correctness",
    "reject_accuracy",
]


def _find_latest_report(eval_runs_dir: Path) -> Path | None:
    """找到最新的 run 目录，返回其 report.json 路径。"""
    if not eval_runs_dir.exists():
        return None
    run_dirs = sorted(
        [d for d in eval_runs_dir.iterdir() if d.is_dir()],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    for run_dir in run_dirs:
        report_path = run_dir / "report.json"
        if report_path.exists():
            return report_path
    return None


def _status_emoji(value: float | None, threshold: float) -> str:
    if value is None:
        return "—"
    return "✅" if value >= threshold else "❌"


def _format_value(value: float | None) -> str:
    if value is None:
        return "—"
    if value <= 1.0:
        return f"{value:.4f}"
    return f"{value:.2f}"


def build_comment(data: dict) -> str:
    summaries = data.get("summaries", [])
    rag_summary = next((s for s in summaries if s["module"] == "rag"), None)
    metrics = rag_summary["metrics"] if rag_summary else {}

    pass_rate = rag_summary["pass_rate"] if rag_summary else 0
    passed = rag_summary["passed"] if rag_summary else 0
    total = rag_summary["total"] if rag_summary else 0

    lines = [
        "## RAG Golden Set 评测结果",
        "",
        f"**通过率**: {pass_rate:.1%} ({passed}/{total})",
        "",
        "| 指标 | 值 | 状态 |",
        "|------|-----|------|",
    ]

    pass_emoji = "✅" if pass_rate >= 0.9 else "❌"
    lines.append(f"| 通过率 | {pass_rate:.1%} ({passed}/{total}) | {pass_emoji} |")

    thresholds = {
        "sem_context_recall": 0.50,
        "sem_faithfulness": 0.50,
        "sem_answer_correctness": 0.40,
        "reject_accuracy": 0.85,
    }

    for key in METRIC_ORDER:
        label = METRIC_DISPLAY.get(key, key)
        val = metrics.get(key)
        emoji = _status_emoji(val, thresholds.get(key, 0.5))
        lines.append(f"| {label} | {_format_value(val)} | {emoji} |")

    for key, label in METRIC_DISPLAY.items():
        if key in METRIC_ORDER:
            continue
        val = metrics.get(key)
        if val is not None:
            lines.append(f"| {label} | {_format_value(val)} | — |")

    lines.extend([
        "",
        "<details><summary>逐用例明细</summary>",
        "",
        "| Case | 状态 | 语义召回 | 忠实度 |",
        "|------|------|---------|--------|",
    ])

    for r in data.get("results", []):
        case_id = r["case_id"]
        status_icon = {"pass": "✅", "fail": "❌", "error": "⚠️", "skip": "○"}.get(
            r["status"], "?"
        )
        recall = r.get("metrics", {}).get("sem_context_recall")
        faith = r.get("metrics", {}).get("sem_faithfulness")
        lines.append(
            f"| {case_id} | {status_icon} | {_format_value(recall)} | {_format_value(faith)} |"
        )

    lines.extend([
        "",
        "</details>",
        "",
        "> 详细报告见 Actions Artifacts（JSON + HTML）",
    ])

    return "\n".join(lines)


def main():
    report_path = _find_latest_report(EVAL_RUNS_DIR)

    if not report_path:
        print(f"未找到评测报告（{EVAL_RUNS_DIR}），跳过 PR 评论")
        return

    with open(report_path, encoding="utf-8") as f:
        data = json.load(f)

    comment = build_comment(data)

    if os.environ.get("GITHUB_ACTIONS") != "true":
        print("非 CI 环境，仅打印评论内容：")
        print(comment)
        return

    try:
        result = subprocess.run(
            ["gh", "pr", "comment", "--body", comment],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "GH_TOKEN": os.environ.get("GH_TOKEN", "")},
        )
        if result.returncode == 0:
            print("PR 评论发送成功")
        else:
            print(f"gh pr comment 失败: {result.stderr}", file=sys.stderr)
    except FileNotFoundError:
        print("gh CLI 未安装，跳过 PR 评论", file=sys.stderr)


if __name__ == "__main__":
    main()
