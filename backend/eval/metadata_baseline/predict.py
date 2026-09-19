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


def _base_metadata() -> dict:
    return {
        "doc_type": "general",
        "confidence": 0.0,
        "business_domain": "general",
        "summary": "",
        "keywords": [],
        "entities": {},
        "time_refs": [],
        "risk": {"level": "none", "signals": []},
    }


def _version_fields() -> dict[str, str]:
    from backend.rag.preprocessing.taxonomy_spec import (
        get_taxonomy,
        metadata_rule_version,
    )
    from backend.rag.preprocessing.metadata_decision import (
        _metadata_model_version,
        _metadata_prompt_version,
    )

    return {
        "taxonomy_version": get_taxonomy().version,
        "rules_version": metadata_rule_version(),
        "model_version": _metadata_model_version(),
        "prompt_version": _metadata_prompt_version(),
    }


async def _predict_row(row: dict, route: str, embedding=None) -> dict:
    t0 = time.monotonic()
    if route == "cascade":
        if embedding is None:
            from backend.rag.embedding_singleton import get_embedding

            embedding = get_embedding()
        from backend.rag.preprocessing.metadata_router import cascade_route
        d = await cascade_route(row["text"], row.get("filename", ""),
                                row.get("file_path", ""), embedding=embedding)
        pred = {**_base_metadata(), **(d.llm_result or {})}
        pred["route_source"] = {
            "L0": "r0", "L1": "r1", "L2": "r2", "L3": "llm"
        }.get(d.level, "unknown")
        pred["decision"] = "accepted" if d.level != "L3" or d.llm_result else "fallback"
        pred["confidence"] = float(pred.get("confidence", d.confidence) or d.confidence)
        if d.level != "L3":
            pred["doc_type"] = d.doc_type or "general"
            pred["business_domain"] = d.evidence.get("domain", "general")
        pred["route_level"] = d.level
        pred["candidates"] = d.evidence.get("top3", [])
        pred["evidence"] = d.evidence
        pred["abstain_reason"] = "" if pred["decision"] == "accepted" else "llm_unavailable"
        pred["llm_call_count"] = 1 if d.level == "L3" else 0
    elif route == "legacy_rule":
        from backend.rag.preprocessing.metadata import (
            classify_with_confidence,
            detect_business_domain,
        )

        doc_type, confidence = classify_with_confidence(
            row["text"], filename=row.get("filename", ""),
            file_path=row.get("file_path", ""))
        pred = {
            **_base_metadata(),
            "doc_type": doc_type,
            "business_domain": detect_business_domain(row["text"])[0],
            "confidence": float(confidence),
            "route_source": "legacy_rule",
            "decision": "accepted",
            "llm_call_count": 0,
        }
    else:
        from backend.rag.preprocessing.metadata_llm import extract_metadata_llm_async
        pred = await extract_metadata_llm_async(row["text"], row.get("filename", ""))
        pred = {**_base_metadata(), **(pred or {})}
        pred["route_source"] = "llm"
        pred["decision"] = "accepted" if pred.get("doc_type") else "fallback"
        pred["llm_call_count"] = 1
    pred.update(_version_fields())
    pred["latency_ms"] = round((time.monotonic() - t0) * 1000, 1)
    return {**row, "pred": pred}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="离线生成统一抽取/级联路由预测")
    ap.add_argument("--golden", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--route", choices=("unified", "cascade", "legacy_rule"),
                    default="unified")
    ap.add_argument("--cascade", action="store_true",
                    help="走级联路由（L0-L2 命中零 LLM；仅预测侧生效）")
    args = ap.parse_args(argv)

    import asyncio

    rows = load_jsonl(args.golden)
    validate_golden(rows)
    out = Path(args.out)
    route = "cascade" if args.cascade else args.route

    async def _run() -> list[dict]:
        embedding = None
        if route == "cascade":
            # 级联评估必须真正加载 embedding；不可用时显式失败，不能用
            # embedding=None 伪造“已评估 R1”的结果。
            from backend.rag.embedding_singleton import get_embedding

            embedding = get_embedding()
        results = []
        for r in rows:
            results.append(await _predict_row(r, route, embedding=embedding))
        return results

    merged = asyncio.run(_run())
    with open(out, "w", encoding="utf-8") as f:
        for m in merged:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    print(f"预测完成: {len(merged)} 条 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
