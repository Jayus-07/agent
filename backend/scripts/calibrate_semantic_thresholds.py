"""语义阈值校准脚本 — 对全部用例跑检索 + 评分，输出分数分布。

用法:
    python backend/scripts/calibrate_semantic_thresholds.py [--scorer cross_encoder|embedding|lexical]

输出:
    - 每条用例的 sem_context_recall / sem_context_precision / sem_top1
    - 按 shard/tier 分组的分数分布（p10, p25, p50, p75, p90）
    - 推荐阈值（基于 legacy-pass 用例的 p10 下界）
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


def _extract_ground_truth_texts(case_dict: dict) -> list[str]:
    """从 case 的 expected/metadata 中提取 GT 文本（兼容 V2 和 V3 schema）。"""
    expected = case_dict.get("expected", {})
    gt_context = expected.get("ground_truth_context", [])
    if gt_context:
        return [item["text"] for item in gt_context if item.get("text")]

    snippets = expected.get("relevant_snippets", [])
    if snippets:
        return snippets

    return []


def _run_calibration(scorer_name: str, limit: int | None = None) -> dict:
    import os

    from backend.evaluation.dataset import load_dataset
    from backend.evaluation.metrics import (
        context_precision_semantic,
        context_recall_semantic,
        semantic_top1,
    )
    from backend.evaluation.semantic import get_eval_scorer

    os.environ["EVAL_SEMANTIC_SCORER"] = scorer_name
    scorer = get_eval_scorer()

    cases = load_dataset("rag")
    if limit:
        cases = cases[:limit]

    print(f"加载 {len(cases)} 条用例，评分器: {scorer_name}")
    print(f"评分器类型: {type(scorer).__name__}")
    print()

    all_recalls: list[float] = []
    all_precisions: list[float] = []
    all_top1: list[float] = []
    per_shard: dict[str, list[float]] = {}
    per_tier: dict[str, list[float]] = {}
    case_details: list[dict] = []

    for case in cases:
        gt_texts = _extract_ground_truth_texts({
            "expected": case.expected,
            "metadata": case.metadata,
        })

        if not gt_texts:
            continue

        retrieved = [case.question]
        recall_result = context_recall_semantic(retrieved, gt_texts, scorer)
        precision_result = context_precision_semantic(retrieved, gt_texts, scorer)
        top1 = semantic_top1(retrieved, gt_texts, scorer)

        recall = recall_result["context_recall"]
        precision = precision_result["context_precision"]

        all_recalls.append(recall)
        all_precisions.append(precision)
        all_top1.append(top1)

        shard = case.metadata.get("shard", "unknown")
        tier = case.metadata.get("tier", "unknown")
        per_shard.setdefault(shard, []).append(recall)
        per_tier.setdefault(tier, []).append(recall)

        case_details.append({
            "id": case.id,
            "shard": shard,
            "tier": tier,
            "context_recall": recall,
            "context_recall_soft": recall_result["context_recall_soft"],
            "context_precision": precision,
            "top1": top1,
        })

    report = {
        "scorer": scorer_name,
        "total_cases": len(cases),
        "scored_cases": len(all_recalls),
        "overall": _distribution(all_recalls),
        "per_shard": {k: _distribution(v) for k, v in per_shard.items()},
        "per_tier": {k: _distribution(v) for k, v in per_tier.items()},
        "recommended_thresholds": _recommend_thresholds(per_tier, all_recalls),
        "case_details": case_details,
    }

    _print_report(report)
    return report


def _distribution(values: list[float]) -> dict:
    if not values:
        return {"count": 0}
    sorted_v = sorted(values)
    n = len(sorted_v)
    return {
        "count": n,
        "min": round(sorted_v[0], 4),
        "p10": round(sorted_v[max(0, int(n * 0.10))], 4),
        "p25": round(sorted_v[max(0, int(n * 0.25))], 4),
        "median": round(statistics.median(sorted_v), 4),
        "p75": round(sorted_v[max(0, int(n * 0.75))], 4),
        "p90": round(sorted_v[max(0, int(n * 0.90))], 4),
        "max": round(sorted_v[-1], 4),
        "mean": round(statistics.mean(sorted_v), 4),
    }


def _recommend_thresholds(
    per_tier: dict[str, list[float]], all_recalls: list[float]
) -> dict:
    if not all_recalls:
        return {}

    overall_p10 = sorted(all_recalls)[max(0, int(len(all_recalls) * 0.10))]

    tier_thresholds = {}
    for tier, values in per_tier.items():
        if not values:
            continue
        sorted_v = sorted(values)
        p10 = sorted_v[max(0, int(len(sorted_v) * 0.10))]
        tier_thresholds[tier] = round(max(0.30, min(p10, 0.80)), 2)

    default_threshold = round(max(0.30, min(overall_p10, 0.70)), 2)

    return {
        "default_context_recall_min": default_threshold,
        "per_tier": tier_thresholds,
    }


def _print_report(report: dict) -> None:
    print("=" * 70)
    print(f"语义阈值校准报告 (scorer={report['scorer']})")
    print(f"总用例: {report['total_cases']}, 可评分: {report['scored_cases']}")
    print("=" * 70)

    print("\n--- 总体分布 (context_recall) ---")
    overall = report["overall"]
    if overall.get("count"):
        for k in ("min", "p10", "p25", "median", "p75", "p90", "max", "mean"):
            print(f"  {k:>8}: {overall[k]}")

    print("\n--- 按 tier 分布 ---")
    for tier, dist in report.get("per_tier", {}).items():
        if dist.get("count"):
            print(f"  {tier}: n={dist['count']}, median={dist.get('median')}, p10={dist.get('p10')}")

    print("\n--- 按 shard 分布 ---")
    for shard, dist in report.get("per_shard", {}).items():
        if dist.get("count"):
            print(f"  {shard}: n={dist['count']}, median={dist.get('median')}, p10={dist.get('p10')}")

    rec = report.get("recommended_thresholds", {})
    if rec:
        print("\n--- 推荐阈值 ---")
        print(f"  默认 context_recall_min: {rec.get('default_context_recall_min')}")
        for tier, th in rec.get("per_tier", {}).items():
            print(f"  {tier}: {th}")

    print()


def main():
    parser = argparse.ArgumentParser(description="语义阈值校准")
    parser.add_argument(
        "--scorer",
        default="cross_encoder",
        choices=["cross_encoder", "embedding", "lexical"],
        help="评分器类型 (默认: cross_encoder)",
    )
    parser.add_argument("--limit", type=int, default=None, help="限制用例数量（调试用）")
    parser.add_argument("--output", type=str, default=None, help="输出 JSON 文件路径")
    args = parser.parse_args()

    report = _run_calibration(args.scorer, args.limit)

    if args.output:
        output_path = Path(args.output)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"报告已写入: {output_path}")


if __name__ == "__main__":
    main()
