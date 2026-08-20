"""
weekly_eval.py — 周度 RAG 评测（2026-08-21 P1-2 迁移到 backend.evaluation）

使用:
    python -m scripts.weekly_eval
    # 或
    python scripts/weekly_eval.py

旧实现基于 backend.eval 的 5 条 golden 用例（端到端、依赖 LLM），已随
backend/eval 一并移除；现统一走 backend.evaluation 的 54 条 rag_test_kb
离线检索评测（确定性、无 API 成本）。手动 CLI 全量评测也可直接用:
    python -m backend.evaluation rag --dataset rag_test_kb.json
"""
import sys
from pathlib import Path

# 把项目根加进 sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.evaluation.weekly import run_weekly_rag_eval


def main():
    print("📝 数据集: rag_test_kb.json (v1.3, 54 条, 离线检索评测)")
    print()

    summary = run_weekly_rag_eval()
    if not summary.get("ok"):
        print(f"❌ 评测失败: {summary.get('error')}")
        sys.exit(1)

    print("=" * 70)
    print("📊 RAG 检索评测结果（backend.evaluation）")
    print("=" * 70)
    print(f"  总数:         {summary['total']}")
    print(f"  通过:         {summary['passed']}（失败 {summary['failed']}）")
    print(f"  通过率:       {summary['pass_rate']:.1%}")
    print(f"  Top-1 准确率: {summary['top1_accuracy']:.1%}")
    print(f"  拒答准确率:   {summary['reject_accuracy']:.1%}")
    print(f"  recall@5:     {summary['recall_at_5']:.1%}")
    print()
    print(f"报告目录: backend/evaluation/results/{summary['timestamp'].replace(':', '-')}")
    print("逐用例详情与 baseline 对比请用: python -m backend.evaluation rag --dataset rag_test_kb.json")


if __name__ == "__main__":
    main()
