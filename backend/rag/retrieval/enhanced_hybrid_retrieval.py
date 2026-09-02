"""Enhanced Hybrid Retrieval - 三路召回 + 自适应阈值集成

替换原有的 hybrid.py::hybrid_retrieve 函数
"""

from typing import List, Tuple, Optional
import logging

logger = logging.getLogger(__name__)


def enhanced_hybrid_retrieve(
    query: str, 
    vector_retriever, 
    bm25_retriever, 
    rule_retriever=None,
    k: int = 8, 
    doc_ids=None, 
    rrf_k: int = 60,
    metadata_filter=None,
    expanded_queries: List[str] = None,
    confidence_aggregator=None,
) -> Tuple[List, dict]:
    """
    增强版混合检索 - 三路召回 + 置信度评估（带 span 生命周期保障）
    
    Returns:
        (docs, {confidence_score, retrieval_strategy, metrics})
    """
    from backend.observability.tracer import trace_collector, SpanName

    span = trace_collector.start_span("enhanced_hybrid_retrieval", name=SpanName.ENHANCED_RETRIEVAL, parent_id=None)
    try:
        return _enhanced_hybrid_retrieve_impl(
            span, query, vector_retriever, bm25_retriever, rule_retriever,
            k, doc_ids, rrf_k, metadata_filter, expanded_queries,
            confidence_aggregator)
    except Exception:
        # P0-2: 旧实现异常路径漏调 end_span，trace 中出现 end_time='' 的
        # 假 success span；这里统一强制收尾并标 error。
        trace_collector.end_span(span, metrics={"status": "error_path"}, status="error")
        raise


def _enhanced_hybrid_retrieve_impl(
    span, query, vector_retriever, bm25_retriever, rule_retriever,
    k, doc_ids, rrf_k, metadata_filter, expanded_queries,
    confidence_aggregator,
) -> Tuple[List, dict]:
    """三路召回主体。span 由外层创建，本函数负责在各出口正常收尾。"""
    from backend.observability.tracer import trace_collector
    from backend.config.rag import (
        VEC_MIN_SCORE,
        ADAPTIVE_VEC_THRESHOLDS,
    )
    
    # Step 1: 查询复杂度分析 → 动态调整参数
    complexity = assess_query_complexity(query)
    base_threshold = complexity["threshold"]
    effective_k = int(k * complexity["k_multiplier"])
    
    logger.info(f"[EnhancedRetrieve] Query='{query[:50]}...', "
               f"complexity={complexity['level']}, "
               f"base_threshold={base_threshold:.2f} (VEC_MIN_SCORE={VEC_MIN_SCORE}), "
               f"k={effective_k}")
    
    docs_list = []
    metrics = {
        "rule_hits": 0,
        "dense_hits": 0,
        "sparse_hits": 0,
        "final_k": effective_k,
        "fallback_used": False,
    }
    
    # Step 2: 三路并行召回（共享线程池，避免每次检索创建/销毁）
    from backend.infra.thread_pools import retrieval_pool_outer
    executor = retrieval_pool_outer()

    # Path A: Rule-Based (仅当启用时)
    if rule_retriever:
        rule_future = executor.submit(rule_retriever.retrieve, query, k=int(effective_k * 0.3))
        metrics["rule_available"] = True
    else:
        rule_future = None
        metrics["rule_available"] = False

    # Path B: Dense Vector Search
    dense_future = executor.submit(vector_retriever.retrieve, query, k=effective_k, doc_ids=doc_ids,
                                    metadata_filter=metadata_filter, expanded_queries=expanded_queries)
    metrics["dense_available"] = True

    # Path C: Sparse Search (BM25+TF-IDF)
    sparse_future = executor.submit(bm25_retriever.invoke, query) if bm25_retriever else None
    if sparse_future:
        metrics["sparse_available"] = True
    
    # Step 3: 收集结果
    try:
        if rule_future:
            rule_docs = rule_future.result() or []
            metrics["rule_hits"] = len(rule_docs)
            docs_list.extend(rule_docs)
    except Exception as e:
        logger.warning(f"[EnhancedRetrieve] Rule retrieval failed: {e}", exc_info=True)
    
    try:
        dense_docs = dense_future.result()
        metrics["dense_hits"] = len(dense_docs)
        docs_list.extend(dense_docs)
    except Exception as e:
        logger.warning(f"[EnhancedRetrieve] Dense retrieval failed: {e}", exc_info=True)
        dense_docs = []
    
    try:
        if sparse_future:
            sparse_docs = sparse_future.result() or []
            metrics["sparse_hits"] = len(sparse_docs)
            docs_list.extend(sparse_docs)
    except Exception as e:
        logger.warning(f"[EnhancedRetrieve] Sparse retrieval failed: {e}", exc_info=True)
    
    # Step 4: Evidence Gate - 检查召回质量
    if not docs_list:
        logger.error("[EnhancedRetrieve] No results from any path - triggering fallback")
        # 降级策略：回到原始 hybrid_retrieve
        from backend.rag.retrieval.hybrid import hybrid_retrieve
        fallback_docs = hybrid_retrieve(query, vector_retriever, bm25_retriever, k=k, doc_ids=doc_ids, rrf_k=rrf_k, metadata_filter=metadata_filter)
        metrics["fallback_used"] = True
        trace_collector.end_span(span, metrics={"status": "fallback"})
        return fallback_docs, {"confidence": 0.0, "strategy": "fallback"}
    
    # Step 5: RRF 融合
    merged_docs = _ultimate_rrf_fusion(docs_list, rrf_k, k)
    
    # Step 6: 计算综合置信度
    overall_confidence = 0.0
    if confidence_aggregator:
        overall_confidence = confidence_aggregator.aggregate(merged_docs, query)
    else:
        # 简易版：直接用平均向量相似度
        vector_scores = [getattr(d, "score", 0) for d in merged_docs[:min(5, len(merged_docs))]]
        overall_confidence = sum(vector_scores) / len(vector_scores) if vector_scores else 0.0
    
    # Step 7: 置信度门控检查
    if overall_confidence < base_threshold:
        logger.warning(f"[EnhancedRetrieve] Low confidence ({overall_confidence:.2f} < {base_threshold:.2f}), "
                      f"triggering query rewrite or扩大检索范围")
        # TODO: 触发 Query Rewrite 或扩大 k 值重试
        metrics["low_confidence_action"] = "expand_k_or_rewrite"
    
    trace_collector.end_span(span, metrics={
        **metrics,
        "overall_confidence": round(overall_confidence, 4),
        "query_complexity": complexity["level"],
    })
    
    return merged_docs, {
        "confidence_score": overall_confidence,
        "retrieval_strategy": "multi_path_hybrid",
        "metrics": metrics,
    }


def _ultimate_rrf_fusion(docs_from_all_paths: List, rrf_k: int, top_k: int) -> List:
    """
    三路召回的统一 RRF 融合（按 chunk_id 去重）
    权重分配：Rule-based > Dense > Sparse；
    无 chunk_type 标记的真实召回按 Dense 同权重 1.0 处理，
    不得静默丢弃（历史缺陷：只认标记导致融合结果恒为空）。
    """
    _WEIGHTS = {
        "rule_match": 2.0,
        "dense_embedding": 1.0,
        "bm25": 0.8,
        "tfidf": 0.8,
    }

    rank_map: dict = {}
    docs_by_id: dict = {}
    groups: dict = {}

    for doc in docs_from_all_paths:
        cid = doc.metadata.get("chunk_id")
        if not cid:
            continue
        if cid not in docs_by_id:
            docs_by_id[cid] = doc
        weight = _WEIGHTS.get(doc.metadata.get("chunk_type", ""), 1.0)
        groups.setdefault(weight, []).append(cid)

    # 同一权重组内按出现次序取 rank，重复出现的同一路只计一次（去重）
    for weight, cids in groups.items():
        seen = set()
        rank = 0
        for cid in cids:
            if cid in seen:
                continue
            seen.add(cid)
            rank += 1
            rank_map[cid] = rank_map.get(cid, 0.0) + weight / (rrf_k + rank)

    # 排序取 Top-K
    sorted_cids = sorted(rank_map.items(), key=lambda x: x[1], reverse=True)

    return [docs_by_id[cid] for cid, _ in sorted_cids[:top_k]]


# =====================================================
# 辅助函数
# =====================================================

def assess_query_complexity(query: str) -> dict:
    """
    查询复杂度评估函数
    """
    import re
    
    char_count = len(query)
    
    # 实体数量统计
    entities = []
    if re.search(r'\d{4}-\d{2}-\d{2}', query):
        entities.append("date")
    if re.search(r'¥\d+(,\d{3})*', query):
        entities.append("amount")
    if re.findall(r'[A-Z]{2,}|[a-z]{4,}', query):
        entities.append("acronym")
    
    # FAQ/条款特征检测
    is_faq_like = bool(re.search(r'Q[:：].*?A[:：]|退货.*?流程 | 退款.*?时效', query, re.IGNORECASE))
    has_clause = bool(re.search(r'第 [一二三四五六七八九十\d]+条', query))
    
    # 复杂度判断
    if char_count < 15 and len(entities) <= 1 and not is_faq_like:
        return {"level": "simple", "threshold": 0.25, "k_multiplier": 1.0}
    elif char_count < 30 and len(entities) <= 2:
        return {"level": "medium", "threshold": 0.30, "k_multiplier": 1.2}
    else:
        return {"level": "complex", "threshold": 0.35, "k_multiplier": 1.5}


class ConfidenceAggregator:
    """多路召回结果的综合置信度评分"""
    
    def __init__(self):
        self.weights = {
            "vector_sim": 0.40,
            "bm25_score": 0.30,
            "doc_type_match": 0.20,
            "entity_coverage": 0.10,
        }
    
    def aggregate(self, docs: List, query: str) -> float:
        """计算整体置信度"""
        scores = {}
        
        # 1. 向量相似度平均分
        vector_scores = [getattr(d, "score", 0) for d in docs[:5]]
        scores["vector_sim"] = sum(vector_scores) / len(vector_scores) if vector_scores else 0
        
        # 2. BM25 分数
        bm25_scores = [d.metadata.get("bm25_score", 0) for d in docs[:5]]
        scores["bm25_score"] = sum(bm25_scores) / len(bm25_scores) if bm25_scores else 0
        
        # 3. 文档类型匹配度
        query_analysis = self._analyze_query_doc_types(query)
        doc_types_found = set(d.metadata.get("doc_type", "") for d in docs[:5])
        scores["doc_type_match"] = self._calc_doc_type_overlap(query_analysis, doc_types_found)
        
        # 4. 实体覆盖度
        query_entities = self._extract_entities(query)
        retrieved_entities = self._extract_retrieved_entities(docs)
        scores["entity_coverage"] = self._calc_entity_coverage(query_entities, retrieved_entities)
        
        # 加权总分
        total = sum(scores[k] * w for k, w in self.weights.items())
        
        return min(total, 1.0)
    
    def _analyze_query_doc_types(self, query: str) -> set:
        try:
            from backend.rag.retrieval.query_analyzer import QueryAnalyzer
            analysis = QueryAnalyzer().analyze(query)
            return set(analysis.doc_types)
        except Exception:
            return set()
    
    def _calc_doc_type_overlap(self, query_types: set, found_types: set) -> float:
        if not query_types:
            return 1.0
        overlap = len(query_types & found_types)
        return overlap / len(query_types)
    
    def _extract_entities(self, text: str) -> set:
        import re
        entities = set()
        numbers = re.findall(r'\d+', text)
        entities.update(numbers)
        acronyms = re.findall(r'[A-Z]{2,}', text)
        entities.update(acronyms)
        return entities
    
    def _extract_retrieved_entities(self, docs: List) -> set:
        all_text = " ".join([d.page_content for d in docs[:5]])
        return self._extract_entities(all_text)
