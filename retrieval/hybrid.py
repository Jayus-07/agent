def _fallback_id(doc) -> str:
    """当 chunk_id 缺失时，用 doc_id + chunk_index 生成回退标识。"""
    did = doc.metadata.get("doc_id", "?")
    ci = doc.metadata.get("chunk_index", 0)
    return f"{did}:{ci}"


def hybrid_retrieve(query, vector_retriever, bm25_retriever, k=5, doc_ids=None, rrf_k=60, metadata_filter=None):
    from retrieval.tracer import trace_collector
    trace_collector._start("hybrid_retrieval")

    vector_docs = vector_retriever.retrieve(query, k=k, doc_ids=doc_ids, metadata_filter=metadata_filter)

    bm25_docs = bm25_retriever.invoke(query)

    if doc_ids:
        bm25_docs = [d for d in bm25_docs if d.metadata.get("doc_id") in doc_ids]

    bm25_docs = bm25_docs[:k*2]

    rank_map = {}

    for rank, doc in enumerate(vector_docs, start=1):
        cid = doc.metadata.get("chunk_id") or _fallback_id(doc)
        rank_map[cid] = rank_map.get(cid, 0) + 1 / (rrf_k + rank)

    for rank, doc in enumerate(bm25_docs, start=1):
        cid = doc.metadata.get("chunk_id") or _fallback_id(doc)
        rank_map[cid] = rank_map.get(cid, 0) + 1 / (rrf_k + rank)

    sorted_cids = sorted(rank_map.items(), key=lambda x: x[1], reverse=True)

    doc_dict = {doc.metadata.get("chunk_id") or _fallback_id(doc): doc for doc in vector_docs + bm25_docs}

    merged = [doc_dict[cid] for cid, _ in sorted_cids[:k]]

    trace_collector._end("hybrid_retrieval", "混合检索",
                         metrics={"vector_hits": len(vector_docs),
                                   "bm25_hits": len(bm25_docs),
                                   "merged_hits": len(merged)})
    return merged


def rrf_fusion_docs(vector_docs, bm25_docs, k=5, rrf_k=60):
    """
    使用RRF算法融合向量检索和BM25检索结果

    参数:
        vector_docs: 向量检索结果列表
        bm25_docs: BM25检索结果列表
        k: 返回文档数量
        rrf_k: RRF平滑参数

    返回:
        融合排序后的文档列表
    """
    if not vector_docs and not bm25_docs:
        return []

    rank_map = {}

    for rank, doc in enumerate(vector_docs, 1):
        doc_id = doc.metadata["doc_id"]
        rank_map[doc_id] = rank_map.get(doc_id, 0) + 1 / (rrf_k + rank)

    for rank, doc in enumerate(bm25_docs, 1):
        doc_id = doc.metadata["doc_id"]
        rank_map[doc_id] = rank_map.get(doc_id, 0) + 1 / (rrf_k + rank)

    sorted_ids = sorted(rank_map, key=lambda x: rank_map[x], reverse=True)
    doc_dict = {doc.metadata["doc_id"]: doc for doc in bm25_docs + vector_docs}

    return [doc_dict[doc_id] for doc_id in sorted_ids[:k]]
