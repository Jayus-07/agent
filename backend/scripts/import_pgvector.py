"""一次性迁移工具：JSONL → pgvector rag_vectors 表（Chroma → pgvector 迁移第 2 步）。

读 export_chroma.py 的产物 data/_migration/chroma_export/*.jsonl，
重写为确定性 ID `{collection}:{doc_id|md5(content)[:12]}:{chunk_index|序号}`
后 upsert 入 PG `rag_vectors`（默认 agent_memory 库），末尾逐 collection
条数对账，差值非零 exit 1。幂等可重跑。

规则:
  - 弃用维度（512 = bge 本地轨，已拍板弃用）跳过导入并在报告单列；
  - metadata 中的非标量值按写入端 _sanitize_metadata 同规则清洗；
  - 只写表，不删数据（重复导入走 ON CONFLICT DO UPDATE）。

用法:
    .venv/Scripts/python.exe backend/scripts/import_pgvector.py \
        [--export data/_migration/chroma_export] [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np
import psycopg2
import psycopg2.extras

from backend.config.database import VECTOR_PG_CONFIG
from backend.rag.vectorstore.knowledge_store import _sanitize_metadata
from backend.rag.vectorstore.pgvector_store import EMBEDDING_DIM
from backend.shared.logger import logger

PROJECT_ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
TABLE = os.getenv("VECTOR_PG_TABLE_PREFIX", "") + "rag_vectors"
SKIP_DIMS = {512}  # bge-small-zh-v1.5 本地轨：已确认弃用（2026-09-17 用户拍板）


def deterministic_id(collection: str, meta: dict, document: str | None, idx: int) -> str:
    doc_id = (meta or {}).get("doc_id")
    if not doc_id:
        doc_id = hashlib.md5((document or "").encode("utf-8")).hexdigest()[:12]
    chunk_idx = (meta or {}).get("chunk_index", idx)
    return f"{collection}:{doc_id}:{chunk_idx}"


def main() -> int:
    parser = argparse.ArgumentParser(description="导入 JSONL 到 pgvector rag_vectors")
    parser.add_argument("--export", default=str(PROJECT_ROOT / "data" / "_migration" / "chroma_export"))
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args()

    export_dir = Path(args.export)
    files = sorted(export_dir.glob("*.jsonl"))
    if not files:
        print(f"[import] {export_dir} 下无 *.jsonl，请先跑 export_chroma.py")
        return 1

    conn = psycopg2.connect(**VECTOR_PG_CONFIG)
    conn.autocommit = True  # CREATE EXTENSION 不能在事务块内
    conn.cursor().execute("CREATE EXTENSION IF NOT EXISTS vector")
    from pgvector.psycopg2 import register_vector  # numpy 向量 → vector 类型适配
    register_vector(conn)
    conn.autocommit = False
    # 建表复用 store 的幂等 DDL（单一定义源，避免脚本与实现漂移）
    from backend.rag.vectorstore.pgvector_store import PgVectorKnowledgeStore
    PgVectorKnowledgeStore(persist_directory="__bootstrap__", embedding_function=None)

    # ---- 汇总读取 ----
    # batch 按 rid 有序 dict：同批内确定性 ID 重复（chroma 历史重索引残留的
    # 同内容冗余行，实测 chunk 级 817 行 / 118 个 ID）自动合并，跨批由 DB
    # upsert 覆盖 → 对账口径 = 去重后 ID 数
    per_col: dict[str, dict] = defaultdict(lambda: {"total": 0, "skipped": 0, "wrong_dim": 0, "merged": 0})
    batch: dict[str, dict[str, tuple]] = defaultdict(dict)
    flushed_ids: dict[str, set] = defaultdict(set)

    def flush(collection: str) -> None:
        rows = batch.pop(collection, None)
        if not rows or args.dry_run:
            return
        flushed_ids[collection].update(rows.keys())
        psycopg2.extras.execute_values(
            conn.cursor(),
            f"""INSERT INTO {TABLE}
                   (id, collection, doc_id, content, metadata, embedding)
               VALUES %s
               ON CONFLICT (id) DO UPDATE SET
                   doc_id = EXCLUDED.doc_id, content = EXCLUDED.content,
                   metadata = EXCLUDED.metadata, embedding = EXCLUDED.embedding""",
            list(rows.values()), template="(%s, %s, %s, %s, %s::jsonb, %s)", page_size=500,
        )

    for fp in files:
        # 文件名格式 {实例目录}__{collection}.jsonl（见 export_chroma.py）。
        # collection 名取实例目录名——与运行时 PgVectorKnowledgeStore 的
        # _collection_name_from_path（persist_directory basename）对齐：
        # data/chroma → "chroma"、data/doc_db → "doc_db"。
        collection = fp.stem.split("__", 1)[0]
        with open(fp, "r", encoding="utf-8") as f:
            seq = 0
            for line in f:
                rec = json.loads(line)
                st = per_col[collection]
                st["total"] += 1
                emb = rec.get("embedding")
                dim = rec.get("dim") or (len(emb) if emb else 0)
                if dim in SKIP_DIMS:
                    st["skipped"] += 1
                    continue
                if dim != EMBEDDING_DIM:
                    st["wrong_dim"] += 1
                    print(f"[import][WARN] {collection}:{rec['id']} 维度 {dim} != {EMBEDDING_DIM}，跳过")
                    continue
                meta = _sanitize_metadata(dict(rec.get("metadata") or {}))
                rid = deterministic_id(collection, meta, rec.get("document"), seq)
                if rid in batch[collection] or rid in flushed_ids[collection]:
                    st["merged"] += 1
                    continue
                batch[collection][rid] = (
                    rid, collection, meta.get("doc_id"),
                    rec.get("document") or "",
                    json.dumps(meta, ensure_ascii=False),
                    np.asarray(emb, dtype=np.float32),
                )
                seq += 1
                if len(batch[collection]) >= 500:
                    flush(collection)

    for col in list(batch.keys()):
        flush(col)
    if not args.dry_run:
        conn.commit()

    # ---- 对账 ----
    print(f"\n[对账] 表: {TABLE}  目标库: {VECTOR_PG_CONFIG['dbname']}  dry_run={args.dry_run}")
    cur = conn.cursor()
    ok = True
    print(f"{'collection':<24}{'源条数':>8}{'跳过512':>9}{'坏维度':>8}{'合并重复':>9}{'导入数':>8}{'PG实数':>8}{'差值':>6}")
    for col in sorted(per_col):
        st = per_col[col]
        expect = st["total"] - st["skipped"] - st["wrong_dim"] - st["merged"]
        if args.dry_run:
            actual = "-"
            diff = "-"
        else:
            cur.execute(f"SELECT count(*) FROM {TABLE} WHERE collection = %s", (col,))
            actual = int(cur.fetchone()[0])
            # 对账口径：PG 内该 collection 全部行（含历史残留）应等于 预期导入数
            diff = actual - expect
            if diff != 0:
                ok = False
        print(f"{col:<24}{st['total']:>8}{st['skipped']:>9}{st['wrong_dim']:>8}{st['merged']:>9}{expect:>8}{str(actual):>8}{str(diff):>6}")
    conn.close()

    if args.dry_run:
        print("\n[dry-run] 未写库。去掉 --dry-run 正式导入。")
        return 0
    if not ok:
        print("\n[import] 对账存在差值 → FAILED（exit 1；排查后可安全重跑，upsert 幂等）")
        return 1
    print("\n[import] 全部 collection 对账 = 0 → OK")
    logger.info(f"[import_pgvector] 对账通过: {dict(per_col)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
