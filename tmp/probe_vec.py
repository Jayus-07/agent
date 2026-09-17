# -*- coding: utf-8 -*-
"""容器内向量空间一致性探针：查询向量 vs rag_vectors 库存向量。"""
from backend.rag.embedding_singleton import get_embedding
from backend.config.database import VECTOR_PG_CONFIG
import psycopg2

q = "公司访客 Wi-Fi 的名称是什么？"
e = get_embedding().embed_query(q)
print("query_dim =", len(e))

conn = psycopg2.connect(**VECTOR_PG_CONFIG)
cur = conn.cursor()
cur.execute("SELECT vector_dims(embedding), count(*) FROM rag_vectors GROUP BY 1")
print("stored_dims:", cur.fetchall())

cur.execute(
    "SELECT collection, round((embedding <=> %s::vector)::numeric, 4) AS dist, "
    "left(metadata->>'file_path', 60) AS fp "
    "FROM rag_vectors ORDER BY dist LIMIT 5",
    (e,),
)
for row in cur.fetchall():
    print("nearest:", row)
conn.close()
