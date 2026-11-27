"""断言评估索引的完整性与维度正确性."""

import os
import sys
import psycopg


def main() -> None:
    expected_dims = int(os.getenv("PROMPT_EVAL_TEST_EMBEDDING_DIMENSIONS", "1024"))
    # 自动复用环境变量中的 PGHOST, PGPORT, PGUSER, PGPASSWORD
    conn = psycopg.connect(dbname=os.environ["MEMORY_PGDATABASE"])
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*), min(vector_dims(embedding)), "
                "max(vector_dims(embedding)) FROM rag_vectors "
                "WHERE collection = 'chroma' AND metadata->>'kb_id' = 'rag_eval_kb'"
            )
            n, dmin, dmax = cur.fetchone()
    finally:
        conn.close()

    problems = []
    if not n:
        problems.append("kb=rag_eval_kb 向量数为 0（导入失败或空集合）")
    if dmin != expected_dims or dmax != expected_dims:
        problems.append(
            f"kb=rag_eval_kb 向量维度 {dmin}..{dmax} 与绑定声明 {expected_dims} 不一致"
        )
    for p in problems:
        print(f"[assert] FAIL {p}")
    if problems:
        print("[assert] EVAL_INDEX_CHECK=failed —— 评测保持 BLOCKED")
        sys.exit(1)
    print(f"[assert] EVAL_INDEX_CHECK=passed vectors={n} dims={expected_dims}")


if __name__ == "__main__":
    main()
