"""run_router_eval.py — Router Evaluation（分层路由 2026-09-22，用户规格 §19）

用法（项目根目录，需 embedding 供应商已绑定）:
    python backend/evaluation/run_router_eval.py            # 全量 23 条
    python backend/evaluation/run_router_eval.py --limit 5  # 冒烟

数据集: backend/evaluation/datasets/router_domain_eval.json
评分维度:
  - Domain Accuracy:            粗分类 top1 == expected.domain
  - Domain Top-2 Recall:        expected.domain 出现在 top2 内
  - Unknown / OOD Recall:       expected=unknown 且分类器拒判（unknown）
  - Clarification Precision:    分类器判 unknown 的用例中 expected=unknown 占比
  - Fine Tool Accuracy:         域内细路由 fine_top1 == expected.tool（含 fast path）
  - Fast Path Rate / Precision: fast path 占比；其中 expected.tool 命中占比
  - Routing Latency:            P50 / P95（粗分类 + 细路由，不含 LLM）

输出: data/eval_reports/router_domain_<ts>.json + 控制台摘要
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DATASET = Path(__file__).parent / "datasets" / "router_domain_eval.json"
REPORT_DIR = ROOT / "data" / "eval_reports"


def load_cases() -> list[dict]:
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    return data["cases"]


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[idx]


def _bootstrap_llm_registry() -> None:
    """独立进程运行时先拉一次 DB 配置层（同 run_tool_routing_eval）。"""
    try:
        import asyncio

        from backend.infra.llm.registry_store import refresh_registry

        asyncio.run(refresh_registry())
    except Exception as e:
        print(f"[bootstrap] refresh_registry 失败（env 兜底）: {e}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条（冒烟）")
    args = parser.parse_args()

    _bootstrap_llm_registry()

    from backend.orchestration.router import get_routing_engine

    router = get_routing_engine()
    cases = load_cases()
    if args.limit:
        cases = cases[: args.limit]

    rows: list[dict] = []
    latencies: list[float] = []
    for case in cases:
        query = case["query"]
        expected = case.get("expected", {})
        exp_domain = expected.get("domain", "unknown")
        exp_tool = expected.get("tool")

        t0 = time.perf_counter()
        decision = router.route(query, {"tenant_id": "evaluation"})
        coarse_ms = (time.perf_counter() - t0) * 1000
        meta = decision.routing_meta or {}

        # RoutingEngine 契约：灰区细路由结果在 selected_tool；规则强信号
        # 直通（decision_source=rule_override）时 selected_tool 为空、命中
        # 能力记在 intent。旧 hierarchical 的 fine_top1/tool_route_mode
        # 已不在 routing_meta（worktree 验收遗留的取数口径，实跑修正）。
        fine_top1 = str(meta.get("selected_tool") or meta.get("intent") or "")
        route_mode = str(meta.get("route_mode") or "")
        latencies.append((time.perf_counter() - t0) * 1000)

        rows.append({
            "query": query,
            "expected_domain": exp_domain,
            "expected_tool": exp_tool,
            "domain": str(meta.get("domain") or "unknown"),
            "confidence": float(meta.get("domain_confidence") or 0.0),
            "margin": float(meta.get("domain_margin") or 0.0),
            "top2": str(meta.get("domain_second") or ""),
            "source": str(meta.get("domain_source") or ""),
            "reason_code": str(meta.get("clarification_reason") or ""),
            "fine_top1": fine_top1,
            "route_mode": route_mode,
            "coarse_ms": round(coarse_ms, 1),
        })

    # ── 评分 ──────────────────────────────────────────────────
    n = len(rows) or 1
    domain_hit = sum(1 for r in rows if r["domain"] == r["expected_domain"])
    top2_hit = sum(
        1 for r in rows
        if r["domain"] == r["expected_domain"]
        or r["top2"] == r["expected_domain"]
    )
    unknown_expected = [r for r in rows if r["expected_domain"] == "unknown"]
    unknown_recall = (
        sum(1 for r in unknown_expected if r["domain"] == "unknown") / len(unknown_expected)
        if unknown_expected else 0.0
    )
    predicted_unknown = [r for r in rows if r["domain"] == "unknown"]
    clarify_precision = (
        sum(1 for r in predicted_unknown if r["expected_domain"] == "unknown")
        / len(predicted_unknown)
        if predicted_unknown else 0.0
    )
    tool_cases = [r for r in rows if r["expected_tool"]]
    tool_hit = sum(1 for r in tool_cases if r["fine_top1"] == r["expected_tool"])
    fast_cases = [r for r in rows if r["route_mode"] == "fast_path"]
    fast_precision = (
        sum(1 for r in fast_cases if r["fine_top1"] == r["expected_tool"]) / len(fast_cases)
        if fast_cases else 0.0
    )

    summary = {
        "total": len(rows),
        "domain_accuracy": round(domain_hit / n, 4),
        "domain_top2_recall": round(top2_hit / n, 4),
        "unknown_recall": round(unknown_recall, 4),
        "clarification_precision": round(clarify_precision, 4),
        "fine_tool_accuracy": round(tool_hit / len(tool_cases), 4) if tool_cases else None,
        "fast_path_rate": round(len(fast_cases) / n, 4),
        "fast_path_precision": round(fast_precision, 4) if fast_cases else None,
        "routing_latency_p50_ms": round(percentile(latencies, 0.5), 1),
        "routing_latency_p95_ms": round(percentile(latencies, 0.95), 1),
    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    mismatches = [
        r for r in rows
        if r["domain"] != r["expected_domain"]
        or (r["expected_tool"] and r["fine_top1"] not in ("", r["expected_tool"]))
    ]
    if mismatches:
        print("\n-- 未命中用例 --")
        for r in mismatches:
            print(json.dumps(r, ensure_ascii=False))

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"router_domain_{int(time.time())}.json"
    report_path.write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nreport: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
