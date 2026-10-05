#!/usr/bin/env python
"""scripts/rag_chunk_fullscan.py — 全量 chunk 质量扫描（P8/P9/P10/P11/P12/F11 取证）

在 rag-service 容器内运行（/opt/venv/bin/python，PG 直查 chunk_store +
doc_registry，quality record 文件抽查），输出 JSON 报告。

用法：
    docker exec agent-rag-service-1 /opt/venv/bin/python \\
        /app/scripts/rag_chunk_fullscan.py --output /tmp/rag-acceptance/chunk-fullscan.json

口径：
  - P8 长度边界：过短(<50)/超长(>2000 字符)占比（无阈值硬门，报告分布）
  - P9 overlap：同文档相邻 chunk 尾首重叠率（Structure 切分 0 重叠属设计，
    Recursive 才有 overlap——报告分布供人工判读，不判 fail）
  - P10/F11 结构语义：travel_guide 的 section_title 非空率 + POI 串味信号
    （section 含 POI A 的 chunk 正文出现其他 POI 专名的比例，信号非定罪）
  - P11 清洗留痕：data/quality_records/*.json 的 cleaning 统计字段存在率
  - P12 处理留痕：doc_registry.pipeline_version/metadata_route 非空率（active）
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import statistics

import psycopg2
import psycopg2.extras

DB = dict(host=os.getenv("PGHOST", "postgres"), port=int(os.getenv("PGPORT", "5432")),
          dbname=os.getenv("PGDATABASE", "agent_memory"),
          user=os.getenv("PGUSER", "postgres"), password=os.getenv("PGPASSWORD", ""))

POI_PROBES = ["三坊七巷", "乌塔", "白塔", "鼓山", "西湖公园", "烟台山", "上下杭", "马尾船政"]


def fetch_rows(cur, sql, args=()):
    cur.execute(sql, args)
    return cur.fetchall()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="/tmp/rag-acceptance/chunk-fullscan.json")
    ap.add_argument("--poi-probe-limit", type=int, default=4000,
                    help="串味信号扫描的 travel_guide chunk 抽样上限")
    args = ap.parse_args()

    conn = psycopg2.connect(**DB)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # ── P8/P9：长度分布 + 相邻重叠（active 文档的 chunk）──
    rows = fetch_rows(cur, """
        SELECT c.doc_id, c.chunk_index, c.char_count, c.content, c.section_title,
               c.doc_type, c.kb_id
        FROM chunk_store c
        JOIN doc_registry r ON r.doc_id = c.doc_id AND r.status = 'active'
        ORDER BY c.doc_id, c.chunk_index
    """)
    lengths = [r["char_count"] for r in rows]
    by_doc: dict[str, list[dict]] = {}
    for r in rows:
        by_doc.setdefault(r["doc_id"], []).append(r)

    overlap_pairs = 0
    overlap_nonzero = 0
    for _doc, chunks in by_doc.items():
        for a, b in zip(chunks, chunks[1:]):
            overlap_pairs += 1
            tail = (a["content"] or "")[-80:]
            if tail and (b["content"] or "").startswith(tail[:40]):
                overlap_nonzero += 1

    p8 = {
        "total_chunks": len(rows),
        "active_docs": len(by_doc),
        "char_p50": int(statistics.median(lengths)) if lengths else 0,
        "char_p95": int(sorted(lengths)[int(len(lengths) * 0.95)]) if lengths else 0,
        "too_short_lt50": sum(1 for x in lengths if x < 50),
        "too_long_gt2000": sum(1 for x in lengths if x > 2000),
    }

    # ── P10/F11：travel_guide 结构留痕 + POI 串味信号 ──
    tg = [r for r in rows if r["doc_type"] == "travel_guide"]
    tg_section_ok = sum(1 for r in tg if (r["section_title"] or "").strip())
    poi_hits: dict[str, int] = {}
    tg_scanned = 0
    for r in tg[: args.poi_probe_limit]:
        text = r["content"] or ""
        sec = r["section_title"] or ""
        tg_scanned += 1
        for poi in POI_PROBES:
            if poi in sec:
                continue  # 本 chunk 主题即该 POI
            if poi in text:
                poi_hits[poi] = poi_hits.get(poi, 0) + 1
    p10 = {
        "travel_guide_chunks": len(tg),
        "section_title_nonempty": tg_section_ok,
        "section_nonempty_rate": round(tg_section_ok / len(tg), 4) if tg else None,
        "poi_crossmention_scanned": tg_scanned,
        "poi_crossmention_counts": poi_hits,
        "note": "串味信号=主题外 POI 专名出现次数（引用/导览语境的合法提及需人工判读，非定罪）",
    }

    # ── P12：active 行处理留痕 ──
    reg = fetch_rows(cur, "SELECT doc_id, pipeline_version, metadata_route, doc_type "
                          "FROM doc_registry WHERE status = 'active'")
    n = len(reg) or 1
    p12 = {
        "active_docs": len(reg),
        "pipeline_version_nonempty": sum(1 for r in reg if (r["pipeline_version"] or "").strip()),
        "metadata_route_nonempty": sum(1 for r in reg if (r["metadata_route"] or "").strip()),
        "pipeline_version_rate": round(sum(1 for r in reg if (r["pipeline_version"] or "").strip()) / n, 4),
        "metadata_route_rate": round(sum(1 for r in reg if (r["metadata_route"] or "").strip()) / n, 4),
    }

    # ── P11：quality record 清洗留痕（容器内文件抽查）──
    qr_files = sorted(glob.glob("/app/data/quality_records/*/*.json"))[:200]
    cleaned_ok = 0
    sample_missing: list[str] = []
    for fp in qr_files:
        try:
            with open(fp, encoding="utf-8") as f:
                rec = json.load(f)
            cl = rec.get("cleaning") or {}
            if isinstance(cl, dict) and cl:
                cleaned_ok += 1
            else:
                sample_missing.append(os.path.basename(fp))
        except Exception:
            sample_missing.append(os.path.basename(fp))
    p11 = {
        "quality_record_files_scanned": len(qr_files),
        "cleaning_block_nonempty": cleaned_ok,
        "rate": round(cleaned_ok / len(qr_files), 4) if qr_files else None,
        "missing_sample": sample_missing[:5],
    }

    report = {
        "ts": os.environ.get("SCAN_TS", ""),
        "p8_length": p8,
        "p9_overlap": {
            "adjacent_pairs": overlap_pairs,
            "nonzero_overlap_pairs": overlap_nonzero,
            "rate": round(overlap_nonzero / overlap_pairs, 4) if overlap_pairs else None,
            "note": "Structure 切分零重叠属设计；Recursive 才有 overlap",
        },
        "p10_structure": p10,
        "p11_cleaning": p11,
        "p12_lineage": p12,
    }
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2)[:2400])
    print(f"\n[fullscan] report -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
