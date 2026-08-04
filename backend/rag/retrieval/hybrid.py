def _fallback_id(doc) -> str:
    """当 chunk_id 缺失时，用 doc_id + chunk_index 生成回退标识。"""
    did = doc.metadata.get("doc_id", "?")
    ci = doc.metadata.get("chunk_index", 0)
    return f"{did}:{ci}"


def hybrid_retrieve(query, vector_retriever, bm25_retriever, k=5, doc_ids=None, rrf_k=60, metadata_filter=None):
    from backend.observability.tracer import trace_collector
    span = trace_collector.start_span("hybrid_retrieval", name="混合检索")

    # 并行执行：Vector 和 BM25 互不依赖
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as ex:
        vf = ex.submit(vector_retriever.retrieve, query, k=k, doc_ids=doc_ids, metadata_filter=metadata_filter)
        bf = ex.submit(bm25_retriever.invoke, query)
        vector_docs = vf.result()
        bm25_docs = bf.result()

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

    # ── Retrieval Debug event ──
    trace_collector.add_event(span, "rrf_fusion", "info",
        f"Vector:{len(vector_docs)} + BM25:{len(bm25_docs)} → RRF:{len(merged)}",
        data={
            "vector_top3": [{"chunk_id": d.metadata.get("chunk_id", ""),
                             "score": round(rank_map.get(d.metadata.get("chunk_id") or _fallback_id(d), 0), 4),
                             "snippet": d.page_content[:120],
                             "source": d.metadata.get("source_file", ""),
                             "doc_type": d.metadata.get("doc_type", "")}
                            for d in vector_docs[:3]],
            "bm25_top3":  [{"chunk_id": d.metadata.get("chunk_id", ""),
                            "snippet": d.page_content[:120],
                            "source": d.metadata.get("source_file", ""),
                            "doc_type": d.metadata.get("doc_type", "")}
                           for d in bm25_docs[:3]],
            "fused_top5": [{"chunk_id": d.metadata.get("chunk_id", ""),
                            "rrf_score": round(rank_map.get(d.metadata.get("chunk_id") or _fallback_id(d), 0), 4),
                            "snippet": d.page_content[:120],
                            "source": d.metadata.get("source_file", ""),
                            "doc_type": d.metadata.get("doc_type", ""),
                            "keywords": d.metadata.get("chunk_keywords", "")}
                           for d in merged[:5]],
        })

    trace_collector.end_span(span,
                         metrics={"vector_hits": len(vector_docs),
                                   "bm25_hits": len(bm25_docs),
                                   "merged_hits": len(merged)})

    # ── Evidence Gate: Retrieval 阶段拒答判定 ────────────────
    # 接入 docs[0].metadata 让下游 chain.py 能读取；
    # 若 docs 为空，下游 chain.py 直接再调一次 gate 处理 NO_EVIDENCE。
    if merged:
        try:
            from backend.rag.evidence_gate import (
                evidence_gate_retrieval, is_evidence_gate_enabled,
                gate_retrieval_passthrough,
            )
            if is_evidence_gate_enabled():
                from backend.rag.retrieval.query_analyzer import QueryAnalyzer
                qa_result = None
                try:
                    qa_result = QueryAnalyzer().analyze(query)
                except Exception:
                    pass
                from backend.config import (
                    VEC_MIN_SCORE, DOC_TYPE_COVERAGE_REQUIRED,
                )
                decision = evidence_gate_retrieval(
                    merged,
                    query_analysis=qa_result,
                    vec_min_score=VEC_MIN_SCORE,
                    require_doc_type_coverage=DOC_TYPE_COVERAGE_REQUIRED,
                )
            else:
                decision = gate_retrieval_passthrough()
            merged[0].metadata["__evidence_gate_decision__"] = decision.to_metrics()
        except Exception as e:
            from backend.shared.logger import logger
            logger.warning(f"[hybrid_retrieve] evidence_gate 评估异常: {e}")

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
