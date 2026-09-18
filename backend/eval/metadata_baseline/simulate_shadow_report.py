"""simulate_shadow_report.py — 影子一致率模拟报告（规划阶段 5 的预演）。

对种子集逐条跑 shadow_route（级联 L0-L2，零 LLM），与主路径统一抽取预测
（preds_unified_seed.jsonl，predict.py 产物）对比 agree/differ，并给出：
  - 各层命中率与一致率（打开级联后的预期行为）；
  - 打开级联后的预估 LLM 节省量（命中条数 / 总量）；
  - 影子分流差异明细（differ 样本清单，供阈值调优先看这些）。

⚠️ 这是**模拟报告**：种子集 52 条 ≠ 生产 7 天 ≥10000 条样本；分布偏斜
（无 table/report 类）。正式切流决策必须基于生产影子 7 天数据
（metadata_route_total{level=~"shadow_.*"}），本报告只回答
"如果现在打开级联，会发生什么"。

用法（仓库根）：
  python -m backend.eval.metadata_baseline.simulate_shadow_report \
      --golden backend/eval/metadata_baseline/golden_seed.jsonl \
      --main-pred backend/eval/metadata_baseline/preds_unified_seed.jsonl \
      --out backend/eval/metadata_baseline/shadow_sim_report.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

from backend.eval.metadata_baseline.evaluate import load_jsonl


async def _simulate(rows: list[dict], main_pred: dict[str, dict]) -> dict:
    from backend.rag.embedding_singleton import get_embedding
    from backend.rag.preprocessing.metadata_router import shadow_route

    emb = get_embedding()
    layer_hits: Counter = Counter()
    agree_by_layer: Counter = Counter()
    differ_rows: list[dict] = []
    miss_no_decision = 0
    errors = 0

    for r in rows:
        d = await shadow_route(r["text"], r.get("filename", ""),
                               r.get("file_path", ""), embedding=emb)
        main = (main_pred.get(r["id"]) or {}).get("doc_type", "general")
        if d is None:
            miss_no_decision += 1
            continue
        layer_hits[d.level] += 1
        agree = d.doc_type == main
        agree_by_layer[f"{d.level}_{'agree' if agree else 'differ'}"] += 1
        if not agree:
            differ_rows.append({
                "id": r["id"], "level": d.level,
                "shadow_doc_type": d.doc_type,
                "shadow_confidence": d.confidence,
                "main_doc_type": main,
                "seed_gold": r.get("doc_type_gold", ""),
            })

    total = len(rows)
    hits = sum(layer_hits.values())
    agrees = sum(v for k, v in agree_by_layer.items() if k.endswith("agree"))
    per_layer = {}
    for level in ("L0", "L1", "L2"):
        n = layer_hits.get(level, 0)
        a = agree_by_layer.get(f"{level}_agree", 0)
        per_layer[level] = {"hits": n, "agree": a, "differ": n - a,
                            "hit_rate": round(n / total, 4) if total else 0,
                            "agreement": round(a / n, 4) if n else None}
    return {
        "n": total,
        "shadow_errors": errors,
        "no_decision": miss_no_decision,
        "overall": {
            "hit_rate": round(hits / total, 4) if total else 0,
            "agreement_when_hit": round(agrees / hits, 4) if hits else None,
            "llm_saved_if_cascade_on": round(hits / total, 4) if total else 0,
        },
        "per_layer": per_layer,
        "differ_detail": differ_rows,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="影子一致率模拟报告")
    ap.add_argument("--golden", required=True)
    ap.add_argument("--main-pred", required=True,
                    help="主路径（统一抽取）预测 JSONL（predict.py 产物）")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    rows = load_jsonl(args.golden)
    main_pred = {p["id"]: p.get("pred") or {} for p in load_jsonl(args.main_pred)}
    report = asyncio.run(_simulate(rows, main_pred))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
