"""backfill_shadow.py — 存量知识库批量影子回灌（规划阶段 5 的数据驱动替代）。

把"等 7 天自然流量"变成主动回灌：对存量文档逐条跑 shadow_route（级联
L0-L2，零 LLM）+ 主路径统一抽取（LLM），对比一致率。

- 抽样：默认按文件名排序 stride 等距抽样（可复现）；--limit 25 为开发阶段
  样本档（用户指示），全量回灌时放开 limit。
- 并发：批内 ≤8 并发 + 批间间隔——回灌实测并发 100 路会触发嵌入限流。
- 成本：每条 1 次 LLM 调用（统一抽取）+ 2 次嵌入调用。

⚠️ 开发阶段样本档结论只用于验证链路与粗调阈值；正式切流决策仍以生产
影子（自然流量 ≥10000 条）为准（规划 §4 阶段 5）。

用法（app 容器内，-w /app）：
  python -m backend.eval.metadata_baseline.backfill_shadow --limit 25 \
      --out backend/eval/metadata_baseline/shadow_backfill_25.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path

BATCH = 8  # 嵌入限流安全并发（实测 100 路并发必挂）

TEXT_EXTS = {".md", ".txt", ".docx", ".pdf", ".csv", ".xlsx"}


def _sample_documents(docs_dir: Path, limit: int) -> list[Path]:
    """stride 等距抽样：按相对路径排序后每 max(1, n//limit) 取 1，保证类型/KB 多样。"""
    files = sorted(
        p for p in docs_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in TEXT_EXTS and not p.name.startswith("."))
    if limit <= 0 or len(files) <= limit:
        return files
    stride = len(files) / limit
    return [files[int(i * stride)] for i in range(limit)]


async def _predict_one(text: str, fname: str, fp: Path, emb) -> dict:
    from backend.rag.preprocessing.metadata_llm import extract_metadata_llm_async
    from backend.rag.preprocessing.metadata_router import shadow_route

    decision = await shadow_route(text, fname, str(fp), embedding=emb)
    llm = await extract_metadata_llm_async(text, fname)
    main_type = (llm or {}).get("doc_type", "general")
    return {
        "shadow": None if decision is None else {
            "level": decision.level, "doc_type": decision.doc_type,
            "confidence": decision.confidence, "evidence": decision.evidence},
        "main": {"doc_type": main_type, "confidence": (llm or {}).get("confidence", 0),
                 "summary_chars": len((llm or {}).get("summary") or ""),
                 "risk_level": (llm or {}).get("risk", {}).get("level"),
                 "latency_ms": None},
    }


async def run(docs_dir: Path, limit: int) -> dict:
    from backend.rag.embedding_singleton import get_embedding
    from backend.rag.preprocessing.parser import parse_file

    emb = get_embedding()
    files = _sample_documents(docs_dir, limit)
    metas: list[tuple[Path, str, str]] = []
    for fp in files:
        try:
            ast = parse_file(str(fp))
            text = (ast.raw_text or "").strip()
        except Exception as e:
            metas.append((fp, "", f"parse_error: {str(e)[:80]}"))
            continue
        metas.append((fp, text[:6000], "" if len(text) >= 50 else "too_short"))

    layer_hits, agree_c, rows = Counter(), Counter(), []
    llm_calls, shadow_fail = 0, 0
    t0 = time.monotonic()
    for i in range(0, len(metas), BATCH):
        if i:
            await asyncio.sleep(3)  # 批间退避：嵌入服务限流窗口是秒级
        batch = metas[i:i + BATCH]
        results = await asyncio.gather(*[
            _predict_one(t, fp.name, fp, emb) if not err else None
            for fp, t, err in batch])
        for (fp, _t, err), res in zip(batch, results):
            rel = str(fp.relative_to(docs_dir))
            if err or res is None:
                rows.append({"file": rel, "error": err or "predict_failed"})
                continue
            llm_calls += 1
            sh, main = res["shadow"], res["main"]
            if sh is None:
                shadow_fail += 1
                rows.append({"file": rel, "main": main["doc_type"], "shadow": None})
                continue
            layer_hits[sh["level"]] += 1
            agree = sh["doc_type"] == main["doc_type"]
            if agree:
                agree_c[sh["level"]] += 1
            rows.append({"file": rel, "level": sh["level"],
                         "shadow": sh["doc_type"], "shadow_confidence": sh["confidence"],
                         "main": main["doc_type"], "main_confidence": main["confidence"],
                         "agree": agree})

    valid = [r for r in rows if r.get("shadow") and r.get("main")]
    th, ta = sum(layer_hits.values()), sum(agree_c.values())
    differs = Counter(f"{r['main']}→{r['shadow']}" for r in valid if not r["agree"])
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "docs_total": len(files), "valid_compared": len(valid),
        "llm_calls": llm_calls, "shadow_fail": shadow_fail,
        "parse_fail": sum(1 for r in rows if r.get("error", "").startswith("parse")),
        "elapsed_min": round((time.monotonic() - t0) / 60, 1),
        "per_layer": {
            lv: {"hits": layer_hits.get(lv, 0), "agree": agree_c.get(lv, 0),
                 "agreement": round(agree_c.get(lv, 0) / layer_hits[lv], 4)
                 if layer_hits.get(lv) else None}
            for lv in ("L0", "L1", "L2")},
        "overall_agreement": round(ta / th, 4) if th else None,
        "llm_saved_if_cascade_on": f"{th}/{len(valid)}",
        "top_disagreements": differs.most_common(10),
        "detail": rows,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="存量知识库批量影子回灌")
    ap.add_argument("--limit", type=int, default=25, help="抽样条数（0=全量）")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    from backend.config.database import DOCS_DIRECTORY
    docs_dir = Path(DOCS_DIRECTORY)
    if not docs_dir.is_dir():
        print(f"ERROR: 存量文档目录不存在: {docs_dir}")
        return 1

    report = asyncio.run(run(docs_dir, args.limit))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
