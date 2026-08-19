def _fallback_id(doc) -> str:
    """当 chunk_id 缺失时，用 doc_id + chunk_index 生成回退标识。"""
    did = doc.metadata.get("doc_id", "?")
    ci = doc.metadata.get("chunk_index", 0)
    return f"{did}:{ci}"


def _filter_by_metadata(docs: list, metadata_filter: dict | None) -> list:
    """按简单 kv 条件过滤文档（metadata_filter 为 {"kb_id": ...} 等简单 dict）。

    BM25 检索不接受 filter 参数，需在结果返回后手动过滤，
    否则不同知识库的残留文档会混入检索结果、挤占 RRF 位置。
    """
    if not metadata_filter:
        return list(docs)
    return [
        d for d in docs
        if all(d.metadata.get(k) == v for k, v in metadata_filter.items())
    ]


def hybrid_retrieve(query, vector_retriever, bm25_retriever, k=5, doc_ids=None, rrf_k=60, metadata_filter=None,
                    expanded_queries: list[str] | None = None):
    """混合检索：Vector + BM25 并行 → RRF 融合。

    2026-08-20: 加 expanded_queries 参数 — 同义词扩展检索。
    vector_retriever.retrieve 时传入扩展 query 列表，CustomRetriever 内部
    对每个 query 各调一次 similarity_search 并 RRF 融合。

    可靠性契约：
      - 单侧失败 → 降级仅用另一侧，span metrics 标记 fallback_side / fallback_reason
      - 两侧都失败 → 抛异常（真系统失败），不伪装成『没有资料』的空召回
    """
    from backend.observability.tracer import trace_collector, SpanName
    from backend.shared.logger import logger
    span = trace_collector.start_span("hybrid_retrieval", name=SpanName.RETRIEVAL)

    # 并行执行：Vector 和 BM25 互不依赖
    from concurrent.futures import ThreadPoolExecutor
    failures: dict[str, BaseException] = {}
    with ThreadPoolExecutor(max_workers=2) as ex:
        vf = ex.submit(vector_retriever.retrieve, query, k=k, doc_ids=doc_ids,
                        metadata_filter=metadata_filter, expanded_queries=expanded_queries)
        bf = ex.submit(bm25_retriever.invoke, query)
        try:
            vector_docs = vf.result()
        except Exception as e:
            # 单侧失败 → 降级仅用另一侧（软降级），留痕；两侧都失败才上抛
            logger.warning(
                f"[hybrid_retrieve] Vector 检索失败，降级仅用 BM25: {e}",
                exc_info=True,
            )
            failures["vector"] = e
            vector_docs = []
        try:
            bm25_docs = bf.result()
        except Exception as e:
            logger.warning(
                f"[hybrid_retrieve] BM25 检索失败，降级仅用 Vector: {e}",
                exc_info=True,
            )
            failures["bm25"] = e
            bm25_docs = []

    if len(failures) == 2:
        # 两侧都失败 = 真系统失败，向上抛（不伪装成『没有资料』的空召回）
        raise RuntimeError(
            f"Vector 与 BM25 检索均失败: "
            f"vector={failures['vector']}, bm25={failures['bm25']}"
        ) from failures["vector"]

    if doc_ids:
        bm25_docs = [d for d in bm25_docs if d.metadata.get("doc_id") in doc_ids]

    # BM25 检索不接受 filter 参数，结果返回后手动按 metadata_filter 过滤，
    # 否则不同知识库的残留文档会混入、挤占 RRF 位置
    bm25_docs = _filter_by_metadata(bm25_docs, metadata_filter)

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

    merged = []
    for cid, rrf_score in sorted_cids[:k]:
        doc = doc_dict[cid]
        doc.metadata["rrf_score"] = round(rrf_score, 4)  # 保留 RRF 分数
        merged.append(doc)

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

    metrics = {"vector_hits": len(vector_docs),
               "bm25_hits": len(bm25_docs),
               "merged_hits": len(merged)}
    if failures:
        # 单侧降级可观测：span metrics 标记 fallback 侧与原因（供 trace/前端定位）
        metrics["fallback_side"] = ",".join(failures.keys())
        metrics["fallback_reason"] = "; ".join(
            f"{k}: {str(v)[:100]}" for k, v in failures.items()
        )
    trace_collector.end_span(span, metrics=metrics)

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
                except Exception as e:
                    # 查询分析失败 → Gate 走无 query_analysis 兜底（软降级），留痕
                    from backend.shared.logger import logger as _logger
                    _logger.debug(f"[hybrid_retrieve] QueryAnalyzer 分析失败: {e}", exc_info=True)
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
