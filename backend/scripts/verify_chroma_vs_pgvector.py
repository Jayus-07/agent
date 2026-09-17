"""一次性校验工具：同向量双跑 Chroma vs pgvector，对比 top5 召回与距离。

从 export JSONL 抽样，用**导出的原始 embedding** 直接作为查询向量分别打
两个引擎（不经过 embedding API → 零外部依赖，纯存储引擎对比）：

  - Chroma: chromadb.PersistentClient(data/chroma).get_collection("langchain")
  - pgvector: rag_vectors WHERE collection='chroma'

指标:
  - recall@5：以 Chroma top5 的 doc_id 集合为基准，PG 命中比例
  - top1 cosine 距离差：量纲一致性（理论都为 1 - cos_sim）

用法:
    .venv/Scripts/python.exe backend/scripts/verify_chroma_vs_pgvector.py [--n 50]
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np
import psycopg2
from pgvector.psycopg2 import register_vector

from backend.config.database import VECTOR_PG_CONFIG
from backend.shared.logger import logger

PROJECT_ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
EXPORT = PROJECT_ROOT / "data" / "_migration" / "chroma_export" / "chroma__langchain.jsonl"
CHROMA_DIR = PROJECT_ROOT / "data" / "chroma"
COLLECTION = "chroma"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=50, help="抽样查询数")
    args = parser.parse_args()

    records = [json.loads(line) for line in open(EXPORT, encoding="utf-8")]
    random.seed(42)
    sample = random.sample(records, min(args.n, len(records)))
    print(f"[verify] 抽样 {len(sample)} 条查询向量（源 {len(records)} 条）")

    import chromadb
    col = chromadb.PersistentClient(path=str(CHROMA_DIR)).get_collection("langchain")

    conn = psycopg2.connect(**VECTOR_PG_CONFIG)
    register_vector(conn)

    recalls, dist_gaps, misses = [], [], []
    for i, rec in enumerate(sample):
        qvec = np.asarray(rec["embedding"], dtype=np.float32)

        # Chroma 侧
        cres = col.query(query_embeddings=[qvec.tolist()], n_results=5,
                         include=["metadatas", "distances"])
        c_ids = [(m or {}).get("doc_id") for m in cres["metadatas"][0]]
        c_dist1 = float(cres["distances"][0][0])

        # pgvector 侧（ef_search 提到 100，避免 HNSW 近似漏召回干扰对比）
        cur = conn.cursor()
        cur.execute("SET hnsw.ef_search = 100")
        cur.execute(
            f"""SELECT doc_id, embedding <=> %s AS dist
                FROM rag_vectors WHERE collection = %s
                ORDER BY embedding <=> %s LIMIT 5""",
            (qvec, COLLECTION, qvec))
        prows = cur.fetchall()
        p_ids = [r[0] for r in prows]
        # pgvector <=> = 1-cos_sim；Chroma cosine distance = 2-2·cos_sim，恒差 2 倍
        # （×2 对齐后再比差值，否则 gap 指标失真）
        p_dist1 = float(prows[0][1]) * 2.0

        c_set, p_set = set(filter(None, c_ids)), set(filter(None, p_ids))
        recall = len(c_set & p_set) / len(c_set) if c_set else 1.0
        recalls.append(recall)
        dist_gaps.append(abs(c_dist1 - p_dist1))
        if recall < 1.0:
            misses.append((rec["id"], recall, sorted(c_set - p_set)))

    conn.close()
    avg_recall = sum(recalls) / len(recalls)
    max_gap = max(dist_gaps)
    print(f"\n[verify] recall@5(以 Chroma 为基准): avg={avg_recall:.4f}  "
          f"min={min(recalls):.4f}  完全一致={sum(1 for r in recalls if r == 1.0)}/{len(recalls)}")
    print(f"[verify] top1 cosine 距离差: max={max_gap:.2e}（<1e-3 即量纲一致）")
    if misses:
        print(f"[verify] 未完全命中样例（前 5）:")
        for rid, r, missing in misses[:5]:
            print(f"   {rid} recall={r:.2f} chroma独有doc_id={missing}")
    ok = avg_recall >= 0.95 and max_gap < 1e-3
    print(f"\n[verify] 结论: {'PASS' if ok else 'FAIL'}（门槛: avg recall@5 ≥ 0.95 且距离差 < 1e-3）")
    logger.info(f"[verify_chroma_vs_pgvector] recall={avg_recall:.4f} max_gap={max_gap:.2e} -> {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
