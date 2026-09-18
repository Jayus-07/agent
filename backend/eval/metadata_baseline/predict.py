"""predict.py — 离线生成统一抽取/级联路由预测文件（规划阶段 1.3 / 5.1）。

一次 LLM 成本生成预测 JSONL，之后 evaluate --pred <file> 可零成本反复回放
（调阈值 / 换 prompt 版本时只需重新生成，不需重跑标注）。

用法（仓库根）：
  # 统一抽取（现状主路径）
  python -m backend.eval.metadata_baseline.predict \
      --golden backend/eval/metadata_baseline/golden.jsonl \
      --out backend/eval/metadata_baseline/preds_unified.jsonl

  # 级联路由（METADATA_CASCADE_ENABLED 逻辑在预测侧强制启用，不动全局配置）
  python -m backend.eval.metadata_baseline.predict --golden ... --out ... --cascade
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from backend.eval.metadata_baseline.evaluate import load_jsonl, validate_golden


async def _predict_row(row: dict, cascade: bool) -> dict:
    t0 = time.monotonic()
    if cascade:
        from backend.rag.preprocessing.metadata_router import cascade_route
        d = await cascade_route(row["text"], row.get("filename", ""),
                                row.get("file_path", ""), embedding=None)
        pred = d.llm_result or {"doc_type": "general", "confidence": 0.0,
                                "business_domain": "general", "summary": "",
                                "keywords": [], "entities": {}, "time_refs": [],
                                "risk": {"level": "none", "signals": []}}
        pred["route_level"] = d.level
    else:
        from backend.rag.preprocessing.metadata_llm import extract_metadata_llm_async
        pred = await extract_metadata_llm_async(row["text"], row.get("filename", ""))
        pred = pred or {"doc_type": "general", "confidence": 0.0,
                        "business_domain": "general", "summary": "",
                        "keywords": [], "entities": {}, "time_refs": [],
                        "risk": {"level": "none", "signals": []}}
    pred["latency_ms"] = round((time.monotonic() - t0) * 1000, 1)
    return {**row, "pred": pred}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="离线生成统一抽取/级联路由预测")
    ap.add_argument("--golden", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cascade", action="store_true",
                    help="走级联路由（L0-L2 命中零 LLM；仅预测侧生效）")
    args = ap.parse_args(argv)

    import asyncio

    rows = load_jsonl(args.golden)
    validate_golden(rows)
    out = Path(args.out)

    async def _run() -> list[dict]:
        results = []
        for r in rows:
            results.append(await _predict_row(r, args.cascade))
        return results

    merged = asyncio.run(_run())
    with open(out, "w", encoding="utf-8") as f:
        for m in merged:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    print(f"预测完成: {len(merged)} 条 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
