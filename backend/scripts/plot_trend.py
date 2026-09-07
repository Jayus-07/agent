"""趋势可视化 — 读取 metrics_history.csv，生成折线图。

可选工具：需要 matplotlib（pip install matplotlib）。
输出：reports/trend.png

用法：
  python backend/scripts/plot_trend.py
  CSV_PATH=custom/path.csv python backend/scripts/plot_trend.py
"""

import csv
import os
import sys
from pathlib import Path

CSV_PATH = Path(os.environ.get("CSV_PATH", "reports/metrics_history.csv"))
OUTPUT_PATH = Path(os.environ.get("OUTPUT_PATH", "reports/trend.png"))

METRIC_COLUMNS = [
    ("pass_rate", "通过率"),
    ("sem_context_recall", "语义召回"),
    ("sem_faithfulness", "忠实度"),
    ("sem_answer_correctness", "答案正确性"),
]


def load_history(csv_path: Path) -> list[dict]:
    if not csv_path.exists():
        return []
    with open(csv_path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def plot_trend(rows: list[dict], output_path: Path) -> Path:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("需要安装 matplotlib: pip install matplotlib", file=sys.stderr)
        sys.exit(1)

    timestamps = [r["timestamp"][:10] for r in rows]
    fig, ax = plt.subplots(figsize=(10, 5))

    for col, label in METRIC_COLUMNS:
        values = []
        for r in rows:
            v = r.get(col, "")
            values.append(float(v) if v else None)
        ax.plot(timestamps, values, marker="o", label=label, linewidth=2)

    ax.set_title("RAG Golden Set 评测趋势", fontsize=14)
    ax.set_xlabel("日期")
    ax.set_ylabel("指标值")
    ax.set_ylim(0, 1.05)
    ax.legend(loc="lower left")
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return output_path


def main() -> None:
    rows = load_history(CSV_PATH)
    if len(rows) < 2:
        print(f"历史数据不足（{len(rows)} 行），至少需要 2 行才能绘制趋势图。")
        sys.exit(0)

    path = plot_trend(rows, OUTPUT_PATH)
    print(f"趋势图已保存: {path}")


if __name__ == "__main__":
    main()
