"""指标持久化 — 读取最新 JSON 报告，追加核心指标到 CSV 历史文件。

CSV 可被 Grafana / InfluxDB Telegraf / Prometheus pushgateway 直接消费，
也可配合 plot_trend.py 本地可视化趋势。

用法：
  python backend/scripts/publish_metrics.py                    # 默认读 data/eval_runs/
  EVAL_RUNS_DIR=custom/path python backend/scripts/publish_metrics.py
"""

import csv
import json
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
EVAL_RUNS_DIR = Path(os.environ.get("EVAL_RUNS_DIR", _PROJECT_ROOT / "data" / "eval_runs"))
HISTORY_FILE = EVAL_RUNS_DIR / "metrics_history.csv"

CSV_COLUMNS = [
    "timestamp",
    "commit_sha",
    "pass_rate",
    "sem_context_recall",
    "sem_faithfulness",
    "sem_answer_correctness",
    "reject_accuracy",
    "total_cases",
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


def extract_metrics(report_path: Path) -> dict:
    with open(report_path, encoding="utf-8") as f:
        data = json.load(f)

    summary = data.get("summaries", [{}])[0]
    metrics = summary.get("metrics", {})
    total = summary.get("total", 0)
    passed = summary.get("passed", 0)

    return {
        "timestamp": data.get("timestamp", ""),
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "sem_context_recall": metrics.get("sem_context_recall"),
        "sem_faithfulness": metrics.get("sem_faithfulness"),
        "sem_answer_correctness": metrics.get("sem_answer_correctness"),
        "reject_accuracy": metrics.get("reject_accuracy"),
        "total_cases": total,
    }


def append_to_csv(row: dict, csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = csv_path.exists()

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def main() -> None:
    report_path = _find_latest_report(EVAL_RUNS_DIR)
    if not report_path:
        print(f"未找到报告文件: {EVAL_RUNS_DIR}/*/report.json", file=sys.stderr)
        sys.exit(1)

    row = extract_metrics(report_path)
    row["commit_sha"] = os.environ.get("GITHUB_SHA", "local")

    append_to_csv(row, HISTORY_FILE)
    print(f"指标已追加到 {HISTORY_FILE}")
    print(f"  pass_rate={row['pass_rate']}, sem_recall={row['sem_context_recall']}, "
          f"faithfulness={row['sem_faithfulness']}, correctness={row['sem_answer_correctness']}")


if __name__ == "__main__":
    main()
