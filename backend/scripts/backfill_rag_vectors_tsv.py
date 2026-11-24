"""backfill_rag_vectors_tsv.py

存量 rag_vectors 数据分批补全 tsv_content 字段：
1. 找出所有 tsv_content IS NULL 的 chunk 记录。
2. 使用结巴分词（cut_for_search）生成带词间空格的纯文本。
3. 批量执行 UPDATE rag_vectors SET tsv_content = to_tsvector('simple', %s) WHERE id = %s。
4. 保证事务分批提交，避免长事务锁表与内存溢出。
"""

from __future__ import annotations

import argparse
import sys
import time
import jieba
import psycopg2
import psycopg2.extras

from backend.config.database import VECTOR_PG_CONFIG
from backend.shared.logger import logger


def backfill_tsv(batch_size: int = 500, max_chunks: int = 0) -> None:
    print(f"[Backfill] 开始回填 rag_vectors.tsv_content (batch_size={batch_size})...")
    conn = psycopg2.connect(**VECTOR_PG_CONFIG)
    conn.autocommit = False

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(1) FROM rag_vectors WHERE tsv_content IS NULL;")
            total_missing = cur.fetchone()[0]
            print(f"[Backfill] 待回填 chunk 总数: {total_missing}")

            if total_missing == 0:
                print("[Backfill] 无缺失 tsv_content 的行，跳过回填。")
                return

        processed = 0
        start_time = time.time()

        while True:
            if max_chunks > 0 and processed >= max_chunks:
                print(f"[Backfill] 已达最大处理上限 {max_chunks}，终止。")
                break

            fetch_limit = min(batch_size, max_chunks - processed) if max_chunks > 0 else batch_size

            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(
                    "SELECT id, content FROM rag_vectors WHERE tsv_content IS NULL LIMIT %s;",
                    (fetch_limit,)
                )
                rows = cur.fetchall()

            if not rows:
                break

            update_params = []
            for r in rows:
                cid = r["id"]
                content = r["content"] or ""
                # 中文结巴分词
                tokens = [t.strip() for t in jieba.cut_for_search(content) if t.strip()]
                tsv_input = " ".join(tokens)
                update_params.append((tsv_input, cid))

            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(
                    cur,
                    "UPDATE rag_vectors SET tsv_content = to_tsvector('simple', %s) WHERE id = %s;",
                    update_params,
                    page_size=100
                )
            conn.commit()

            processed += len(rows)
            elapsed = time.time() - start_time
            qps = processed / elapsed if elapsed > 0 else 0.0
            print(f"[Backfill] 进度: {processed}/{total_missing} ({processed / total_missing * 100:.1f}%) - {qps:.1f} chunks/s")

        print(f"[Backfill] 回填完毕！共处理 {processed} 条 chunk，耗时 {time.time() - start_time:.2f}s。")

    except Exception as e:
        conn.rollback()
        print(f"[Backfill] 异常回滚: {e}", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="回填 rag_vectors.tsv_content")
    parser.add_argument("--batch-size", type=int, default=500, help="每批更新数量")
    parser.add_argument("--max-chunks", type=int, default=0, help="最多处理数量(0为不限)")
    args = parser.parse_args()
    backfill_tsv(batch_size=args.batch_size, max_chunks=args.max_chunks)
