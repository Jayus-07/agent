"""build_review_workpackage.py — 标注复核工作包生成（规划阶段 1.1 推进）。

把「双人复核 104 条全量」压缩为「只复核机器分歧样本」：
  Part A（种子 52 条）：规则链 + 统一抽取双链预测 vs seed gold 三方对比，
    三方一致 → confirmed（人工免检）；任一分歧 → dispute（人工必看）。
  Part B（skip 48 条）：统一抽取预标 + 置信度分级 ——
    confidence ≥ 0.85 → candidate（复核确认即可，改动量小）；
    confidence < 0.85 → needs_review（人工从头标）。

产出：
  annotation_workpackage.json  — 机器可读工作包（逐条三方结果/预标）
  标注排期估算按 Q4 未确认默认（200 条/周）在 stdout 汇总。

用法（仓库根）：
  python -m backend.eval.metadata_baseline.build_review_workpackage
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from backend.eval.metadata_baseline.build_seed_from_fixtures import (
    SKIP_REASON, extract_text)
from backend.eval.metadata_baseline.evaluate import load_jsonl, run_rule_predictions


async def _prelabel_skipped(skipped_files: list[dict]) -> list[dict]:
    """skip 文件统一抽取预标（LLM）；解析失败/空文本的跳过。"""
    from backend.rag.preprocessing.metadata_llm import extract_metadata_llm_async
    from backend.rag.preprocessing.metadata_schema import DOC_TYPES

    files_dir = None
    out = []
    for s in skipped_files:
        name = s["file"]
        # 在 fixture 目录中定位文件（rglob 兜底子目录）
        fp = next((p for p in _FIXTURE_ROOT.rglob(name) if p.is_file()), None)
        if fp is None:
            out.append({**s, "prelabel": None, "grade": "unresolvable"})
            continue
        try:
            text = extract_text(fp)
        except Exception as e:
            out.append({**s, "prelabel": None, "grade": "unresolvable",
                        "error": str(e)[:100]})
            continue
        if len(text) < 50:
            out.append({**s, "prelabel": None, "grade": "unresolvable",
                        "reason": "文本过短"})
            continue
        pred = await extract_metadata_llm_async(text, name)
        if not pred or pred.get("doc_type") not in DOC_TYPES:
            out.append({**s, "prelabel": None, "grade": "needs_review"})
            continue
        conf = float(pred.get("confidence", 0))
        out.append({**s, "file_path": str(fp), "text_chars": len(text),
                    "prelabel": {"doc_type": pred["doc_type"], "confidence": conf},
                    "grade": "candidate" if conf >= 0.85 else "needs_review"})
    return out


_FIXTURE_ROOT = None


def main(argv: list[str] | None = None) -> int:
    global _FIXTURE_ROOT
    repo_root = Path(__file__).resolve().parents[3]
    base = Path(__file__).parent
    _FIXTURE_ROOT = repo_root / "backend" / "evaluation" / "fixtures" / "rag_100_docs"

    ap = argparse.ArgumentParser(description="标注复核工作包生成")
    ap.add_argument("--golden", default=str(base / "golden_seed.jsonl"))
    ap.add_argument("--main-pred", default=str(base / "preds_unified_seed.jsonl"))
    ap.add_argument("--skipped", default=str(base / "golden_seed_skipped.json"))
    ap.add_argument("--out", default=str(base / "annotation_workpackage.json"))
    args = ap.parse_args(argv)

    seeds = load_jsonl(args.golden)
    unified = {p["id"]: (p.get("pred") or {}) for p in load_jsonl(args.main_pred)}
    skipped = json.loads(Path(args.skipped).read_text(encoding="utf-8"))

    # Part A：种子三方对比（规则链本地跑，毫秒级）
    rule_rows = run_rule_predictions(seeds)
    confirmed, disputes = [], []
    for r in rule_rows:
        rid = r["id"]
        g = r["doc_type_gold"]
        rule_t = r["pred"]["doc_type"]
        uni_t = (unified.get(rid) or {}).get("doc_type", "general")
        entry = {"id": rid, "seed_gold": g, "rule_pred": rule_t,
                 "unified_pred": uni_t,
                 "unified_confidence": (unified.get(rid) or {}).get("confidence")}
        if rule_t == g == uni_t:
            confirmed.append(entry)
        else:
            entry["dispute_pattern"] = (
                "all_differ" if (rule_t != g and uni_t != g)
                else "rule_off" if rule_t != g else "unified_off")
            disputes.append(entry)

    # Part B：skip 预标（LLM 48 次，~1.5min）
    prelabels = asyncio.run(_prelabel_skipped(skipped))
    candidates = [p for p in prelabels if p.get("grade") == "candidate"]
    needs_review = [p for p in prelabels if p.get("grade") in ("needs_review", "unresolvable")]

    pkg = {
        "generated_at": "2026-09-19",
        "part_a_seed": {"total": len(seeds), "confirmed": len(confirmed),
                        "disputes": disputes},
        "part_a_confirmed": confirmed,
        "part_b_skipped": {"total": len(skipped),
                           "candidates": len(candidates),
                           "needs_review": len(needs_review)},
        "part_b_detail": prelabels,
    }
    Path(args.out).write_text(json.dumps(pkg, ensure_ascii=False, indent=2),
                              encoding="utf-8")

    # 排期估算（Q4 未确认默认：双人 200 条/周；仲裁每条另计 0.5 人日按争议率）
    manual = len(disputes) + len(needs_review)
    confirm_only = len(candidates)
    print(json.dumps({
        "seed_confirmed_免检": len(confirmed),
        "seed_disputes_人工仲裁": len(disputes),
        "skip_candidates_复核确认": len(candidates),
        "skip_needs_review_从头标": len(needs_review),
        "人工工作量_条": manual,
        "复核确认_条": confirm_only,
        "按Q4默认200条每周_估_周": round((manual + confirm_only * 0.2) / 200, 2),
        "out": args.out,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
