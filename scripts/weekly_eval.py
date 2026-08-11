"""
weekly_eval.py — 周度 RAG 评测（2026-08-11 P1 Golden Dataset）

使用:
    python -m scripts.weekly_eval
    # 或
    python scripts/weekly_eval.py

建议用 cron 每周日跑:
    0 2 * * 0 cd /path/to/agent && python -m scripts.weekly_eval
"""
import argparse
import json
import sys
import time
from pathlib import Path

# 把项目根加进 sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.eval import (
    DEFAULT_DATASET,
    load_golden,
    run_golden_eval,
    history,
)


# 重新定位默认数据集（脚本可能从仓库根跑）
EVAL_DIR = Path(__file__).resolve().parent.parent / "backend" / "eval"
DEFAULT_DATASET = EVAL_DIR / "golden_v1.json"


def print_summary(summary: dict) -> None:
    """打印可读的评测摘要。"""
    print("=" * 70)
    print(f"📊 Golden Dataset 评测结果")
    print("=" * 70)
    print(f"  总数:     {summary['total']}")
    print(f"  命中率:   {summary['hit_rate']:.1%}")
    print(f"  拒答率:   {summary['reject_rate']:.1%}")
    print(f"  Topdoc 命中率: {summary['top_doc_hit_rate']:.1%}")
    print(f"  通过率:   {summary['pass_rate']:.1%}")
    print(f"  平均延迟: {summary['avg_latency_ms']}ms")
    print()
    print("通过/失败明细:")
    for r in summary["details"]:
        status = "✅" if r["passed"] else "❌"
        top1 = f"{r['top1_score']:.3f}" if r["top1_score"] is not None else "—"
        print(f"  {status} {r['case_id']:8s} {r['query'][:30]:30s} hit={int(r['hit'])} rej={int(r['rejected'])} top1={top1}")
    print()


def main():
    parser = argparse.ArgumentParser(description="RAG Golden Dataset 评测")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET), help="评测集路径")
    parser.add_argument("--history-days", type=int, default=30, help="历史趋势天数")
    parser.add_argument("--no-history", action="store_true", help="不打印历史")
    args = parser.parse_args()

    print(f"📝 数据集: {args.dataset}")
    print()

    # 验证数据集
    try:
        cases = load_golden(args.dataset)
    except Exception as e:
        print(f"❌ 加载数据集失败: {e}")
        sys.exit(1)
    print(f"✅ 加载 {len(cases)} 条 case")
    print()

    # 跑评测
    t0 = time.time()
    summary = run_golden_eval(args.dataset)
    elapsed = time.time() - t0

    print_summary(summary)
    print(f"⏱️  总耗时: {elapsed:.1f}s")

    # 历史趋势
    if not args.no_history:
        trends = history(days=args.history_days)
        if trends:
            print()
            print(f"📈 最近 {args.history_days} 天历史趋势（{len(trends)} 次）:")
            for t in trends[:5]:
                print(f"  {t['run_at']}  pass={t['pass_rate']:.0%}  hit={t['hit_rate']:.0%}")


if __name__ == "__main__":
    main()
