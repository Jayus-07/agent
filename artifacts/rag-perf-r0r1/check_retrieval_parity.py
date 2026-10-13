"""检索等价性对照（R1-A 验收）：向量逐位相等 + 同一问题召回集与名次一致。

1) 绕过缓存直连 provider 的向量与经缓存返回的向量做 float32 逐位比较；
2) 用 pgvector similarity_search_with_score 记录同一问题 Top-K 的
   chunk_id/doc_id/score，基线版与优化版输出可直接 diff。
只读，不写任何业务数据。
"""
import asyncio
import hashlib
import json
import struct
import sys


def _fp32_bits(vec):
    import numpy as np

    return hashlib.sha256(np.asarray(vec, dtype=np.float32).tobytes()).hexdigest()


def main() -> int:
    question = sys.argv[1] if len(sys.argv) > 1 else "退款审核时间是多少？"
    from backend.infra.llm import registry_store

    asyncio.run(registry_store.refresh_registry())
    from backend.rag.embedding_singleton import get_embedding
    from backend.rag.pipeline import get_rag_pipeline

    emb = get_embedding()
    # 1) 向量逐位等价：先直连 provider，再走缓存路径，比较 float32 位模式
    texts = [question, "现货商品的发货时间", "退款到账时间", "差评处理流程"]
    vector_checks = []
    for t in texts:
        direct = list(emb._inner.embed_query(t))
        cached = list(emb.embed_query(t))
        vector_checks.append({
            "text_sha": hashlib.sha256(t.encode()).hexdigest()[:12],
            "direct_fp32": _fp32_bits(direct),
            "cached_fp32": _fp32_bits(cached),
            "equal": _fp32_bits(direct) == _fp32_bits(cached),
            "max_abs_diff": float(max(abs(a - b) for a, b in zip(direct, cached))),
        })
    # 2) 召回集与名次：直接走向量库（不生成、不写库）
    pipeline = get_rag_pipeline()
    store = getattr(pipeline, "vectordb", None)
    rerank = []
    if store is not None:
        hits = store.similarity_search_with_score(question, k=8)
        for rank, (doc, score) in enumerate(hits, 1):
            meta = getattr(doc, "metadata", None) or {}
            rerank.append({
                "rank": rank,
                "chunk_id": meta.get("chunk_id"),
                "doc_id": meta.get("doc_id"),
                "source": meta.get("source_file") or meta.get("source"),
                "pgvector_distance_x2": round(float(score), 6),
            })
    print(json.dumps({
        "question": question,
        "vector_checks": vector_checks,
        "vector_all_equal": all(c["equal"] for c in vector_checks),
        "topk": rerank,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
