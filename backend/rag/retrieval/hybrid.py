from backend.shared.logger import logger

import threading

# 增强检索递归护栏：enhanced 路径内部空召回时会回调原始 hybrid_retrieve，
# 若此时再次进入增强分支会无限递归。按线程隔离，防止并发串扰。
_enhanced_inflight = threading.local()


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


import re


# =====================================================
# 三层查询路由分类器
# =====================================================
# vector_only:         简单 FAQ → 纯向量检索（跳过 BM25 + MultiQuery）
# hybrid:              普通查询 / 精确标识符 → Vector + BM25 + RRF
# hybrid_multi_query:  复杂 / 多意图 / 多跳 → Vector + BM25 + LLM 改写多路召回

# 精确标识符正则：检测到任一则 HYBRID（BM25 精确匹配有不可替代的价值）
# 注意：
# 1. 全部要求大写/明确边界，避免误伤自然语言中的英文单词
# 2. 使用 [A-Za-z0-9] 而非 \w，因为 \w 在 Python 中默认匹配 Unicode（含中文）
_EXACT_IDENTIFIER_PATTERNS = [
    re.compile(r'\b[A-Z]{2,}[-_]\d{3,}\b'),                      # SKU/型号: AB-1234, XY_5678（须带连字符/下划线）
    re.compile(r'(?:订单|单号|流水号)[号:]?\s*[A-Za-z0-9]{6,}'),   # 订单号（仅英文数字，不匹配中文）
    re.compile(r'(?:错误码|错误号|error\s*code)[号:]?\s*[A-Za-z0-9]+', re.I),  # 错误码（仅英文数字）
    re.compile(r'(?:保单|合同)(?:(?:编号|号|ID)[:：]?\s*|[:：]\s*)[A-Z0-9][A-Z0-9_-]{3,}', re.I),  # 保单/合同编号（须有"编号/号/ID"标签或冒号分隔）
    re.compile(r'\bv\d+\.\d+(?:\.\d+)?\b', re.I),                 # 版本号: v2.1, v3.0.1（须 v 前缀）
    re.compile(r'\b[A-Z]{1,4}\d{3,6}\b'),                         # 型号: A1234, AB5678（大写+3位以上数字+词边界）
    re.compile(r'(?:条款|条例|法规)\s*第?\s*\d+[条款章节]'),        # 法律条款引用
    re.compile(r'\b0x[A-Fa-f0-9]{4,}\b'),                         # 十六进制错误码: 0x80004005
    re.compile(r'\b[A-Z]{2,}_\d{2,}\b'),                          # 下划线格式: ERR_1234, CODE_5678
]

# 复杂查询信号：多意图 / 对比 / 多跳推理 → HYBRID_MULTI_QUERY
_COMPLEX_PATTERNS = [
    "分析", "对比", "比较", "总结", "汇总", "概述",
    "全部", "所有", "区别", "差异", "不同",
    "优缺点", "利弊", "优劣",
    "关系", "影响", "作用", "意义",
    "以及", "同时", "另外", "还有", "并且",
]

# 多问号 / 多句子 → 多意图
_MULTI_QUESTION_THRESHOLD = 2


def _classify_query_tier(query: str) -> str:
    """三层查询分类。统一控制 BM25 开关 + MultiQuery 触发。

    优先级：Tier 3（复杂意图）→ Tier 2（精确标识符）→ Tier 1（兜底）
    复杂意图优先：含"分析""对比"等多意图查询即使包含产品型号，
    也需要 LLM 改写出多个子查询，而非仅靠 BM25 精排。

    Returns:
        "vector_only" | "hybrid" | "hybrid_multi_query"
    """
    from backend.config.rag import ADAPTIVE_RETRIEVAL_MODE

    if ADAPTIVE_RETRIEVAL_MODE in ("vector_only", "hybrid", "hybrid_multi_query"):
        return ADAPTIVE_RETRIEVAL_MODE

    q = query.strip()

    # ── Tier 3: 复杂 / 多意图 → HYBRID_MULTI_QUERY（最高优先级）──
    # 多个问号/句子（多意图）
    question_marks = q.count("？") + q.count("?")
    # 过滤空字符串，避免 "问题？" split 后得到 ["问题", ""] 误判
    sentence_parts = [
        x.strip() for x in re.split(r'[。！？；!?;]', q) if x.strip()
    ]
    if question_marks >= _MULTI_QUESTION_THRESHOLD or len(sentence_parts) >= 3:
        return "hybrid_multi_query"

    # 复杂推理关键词
    for pat in _COMPLEX_PATTERNS:
        if pat in q:
            return "hybrid_multi_query"

    # ── Tier 2: 精确标识符 → HYBRID ──
    # 错误码/订单号/SKU 等在知识库中通常有标准文档，
    # Vector + BM25 精排效果远好于纯向量检索
    for pattern in _EXACT_IDENTIFIER_PATTERNS:
        if pattern.search(q):
            return "hybrid"

    # ── Tier 1: 简单 FAQ → VECTOR_ONLY ──
    return "vector_only"


# 向后兼容别名
def _classify_query_mode(query: str) -> str:
    """Deprecated: 使用 _classify_query_tier()。返回 'vector_only' 或 'hybrid'。"""
    tier = _classify_query_tier(query)
    if tier == "hybrid_multi_query":
        return "hybrid"
    return tier


def _evaluate_retrieval_gate(merged: list, query: str):
    """Retrieval 阶段 Gate 评估，返回 GateDecision（总开关关闭时透传判定）。

    量纲修正（2026-09-03 事故修复）：注入时点文档通常只携带 rrf_score
    （RRF 量纲，上限 ≈0.033），与 VEC_MIN_SCORE（余弦相似度量纲，默认 0.2）
    不可比 —— 若文档无 rerank_score/similarity 等语义分数，跳过分数阈值
    检查，交由带真实分数的下游 chain 层 Gate 判定，避免必拒。
    """
    from backend.rag.evidence_gate import (
        evidence_gate_retrieval,
        gate_retrieval_passthrough,
        is_evidence_gate_enabled,
    )

    if not is_evidence_gate_enabled():
        return gate_retrieval_passthrough()

    qa_result = None
    try:
        from backend.rag.context import get_context
        qa_result = get_context().query_analysis
    except Exception:
        pass
    if qa_result is None:
        try:
            from backend.rag.retrieval.query_analyzer import QueryAnalyzer
            qa_result = QueryAnalyzer().analyze(query)
        except Exception as e:
            # 查询分析失败 → Gate 走无 query_analysis 兜底（软降级），留痕
            logger.debug(f"[hybrid_retrieve] QueryAnalyzer 分析失败: {e}", exc_info=True)

    from backend.config import DOC_TYPE_COVERAGE_REQUIRED, VEC_MIN_SCORE

    has_semantic_score = any(
        d.metadata.get("rerank_score") is not None
        or d.metadata.get("similarity") is not None
        for d in merged
    )
    return evidence_gate_retrieval(
        merged,
        query_analysis=qa_result,
        vec_min_score=VEC_MIN_SCORE if has_semantic_score else 0.0,
        require_doc_type_coverage=DOC_TYPE_COVERAGE_REQUIRED,
    )


def hybrid_retrieve(query, vector_retriever, bm25_retriever, k=5, doc_ids=None, rrf_k=60, metadata_filter=None,
                    expanded_queries: list[str] | None = None):
    """增强版混合检索 - 自动启用三路召回（Rule + Dense + Sparse）

    P2 优化集成点：
      - 从 enhanced_hybrid_retrieval 导入核心逻辑
      - 当 ADAPTIVE_THRESHOLD_ENABLED=true 时启用动态阈值
      - Rule-based retriever 仅对 FAQ/条款类查询激活
    """
    from backend.config.rag import ADAPTIVE_THRESHOLD_ENABLED, CONFIDENCE_AGGREGATOR_ENABLED
    
    # 尝试启用增强检索（如果配置开启且依赖可用；已在增强路径内则不再进入）
    if (ADAPTIVE_THRESHOLD_ENABLED and CONFIDENCE_AGGREGATOR_ENABLED
            and not getattr(_enhanced_inflight, "active", False)):
        try:
            logger.info(f"[hybrid_retrieve] Using ENHANCED multi-path retrieval for query='{query[:50]}...'")
            from backend.rag.retrieval.enhanced_hybrid_retrieval import (
                enhanced_hybrid_retrieve as enhanced_retrieve,
                ConfidenceAggregator,
            )
            
            confidence_aggregator = ConfidenceAggregator() if CONFIDENCE_AGGREGATOR_ENABLED else None
            _enhanced_inflight.active = True
            try:
                docs, meta = enhanced_retrieve(
                    query, vector_retriever, bm25_retriever,
                    k=k, doc_ids=doc_ids, rrf_k=rrf_k, metadata_filter=metadata_filter,
                    expanded_queries=expanded_queries,
                    confidence_aggregator=confidence_aggregator,
                )
            finally:
                _enhanced_inflight.active = False
            
            # 将 confidence 信息注入 metadata 供下游使用
            for i, doc in enumerate(docs[:3]):
                doc.metadata[f"enhanced_confidence_{i}"] = meta.get("confidence_score", 0)
            
            return docs
            
        except ImportError as e:
            logger.warning(f"[hybrid_retrieve] Enhanced retrieval import failed ({e}), fallback to original")
        except Exception as e:
            logger.warning(f"[hybrid_retrieve] Enhanced retrieval error ({e}), falling back to original")
    
    # Fallback: 原始 hybrid 逻辑
    from backend.observability.tracer import SpanName, trace_collector
    span = trace_collector.start_span("hybrid_retrieval", name=SpanName.HYBRID_RETRIEVAL, parent_id=None)

    # ── 三层查询路由：vector_only / hybrid / hybrid_multi_query ──
    query_tier = _classify_query_tier(query)
    logger.info(f"[hybrid_retrieve] query_tier={query_tier} for query='{query[:50]}...'")

    if query_tier == "vector_only":
        # Vector-only 路径：优先纯向量，避免关键词噪声稀释语义信号；
        # 但向量失败/空召回时降级 BM25 兜底（软降级，不伪装成『没有资料』）
        from backend.infra.thread_pools import retrieval_pool_inner
        ex = retrieval_pool_inner()
        failures: dict[str, BaseException] = {}
        try:
            vector_docs = ex.submit(
                vector_retriever.retrieve, query, k=k, doc_ids=doc_ids,
                metadata_filter=metadata_filter, expanded_queries=expanded_queries,
            ).result()
        except Exception as e:
            logger.warning(
                f"[hybrid_retrieve] Vector-only 检索失败，降级 BM25: {e}",
                exc_info=True,
            )
            failures["vector"] = e
            vector_docs = []

        vector_docs = _filter_by_metadata(vector_docs, metadata_filter)
        if doc_ids:
            vector_docs = [d for d in vector_docs if d.metadata.get("doc_id") in doc_ids]

        bm25_docs: list = []
        if not vector_docs:
            # 向量空召回/失败 → BM25 兜底召回
            try:
                bm25_docs = bm25_retriever.invoke(query)
                bm25_docs = _filter_by_metadata(bm25_docs, metadata_filter)
                if doc_ids:
                    bm25_docs = [d for d in bm25_docs
                                 if d.metadata.get("doc_id") in doc_ids]
            except Exception as e:
                logger.warning(
                    f"[hybrid_retrieve] Vector-only 降级后 BM25 也失败: {e}",
                    exc_info=True,
                )
                failures["bm25"] = e
                bm25_docs = []

        if len(failures) == 2:
            # 向量 + BM25 都失败 = 真系统失败，向上抛（与 hybrid 路径口径一致）
            raise RuntimeError(
                f"Vector 与 BM25 检索均失败: "
                f"vector={failures['vector']}, bm25={failures['bm25']}"
            ) from failures["vector"]

        merged = (vector_docs or bm25_docs)[:k]

        # 注入 rrf_score 占位（与 hybrid 路径 metadata 协议对齐）
        for rank, doc in enumerate(merged, start=1):
            doc.metadata["rrf_score"] = round(1 / (rrf_k + rank), 4)
            doc.metadata["retrieval_mode"] = "vector_only"

        if vector_docs:
            event_msg = f"Vector-only: {len(vector_docs)} docs (BM25 skipped)"
        else:
            event_msg = f"Vector-only 空召回/失败，BM25 fallback: {len(bm25_docs)} docs"
        trace_collector.add_event(span, "vector_only", "info", event_msg)

        metrics = {
            "query_tier": "vector_only",
            "vector_hits": len(vector_docs),
            "bm25_hits": len(bm25_docs),
            "merged_hits": len(merged),
        }
        if failures:
            # 单侧降级可观测：span metrics 标记 fallback 侧与原因
            metrics["fallback_side"] = ",".join(failures.keys())
            metrics["fallback_reason"] = "; ".join(
                f"{side}: {str(err)[:100]}" for side, err in failures.items()
            )
        trace_collector.end_span(span, metrics=metrics)

        if merged:
            try:
                decision = _evaluate_retrieval_gate(merged, query)
                merged[0].metadata["__evidence_gate_decision__"] = decision.to_metrics()
            except Exception as e:
                logger.warning(f"[hybrid_retrieve] evidence_gate 评估异常: {e}")
        return merged

    # 财务 SQL 旁路检索：查询含财务指标 + 数值条件时并行执行
    sql_docs: list = []
    sql_bypass_used = False
    try:
        from backend.config import FINANCIAL_SQL_BYPASS_ENABLED
        if FINANCIAL_SQL_BYPASS_ENABLED:
            sql_docs = _financial_sql_bypass(query)
            if sql_docs:
                sql_bypass_used = True
                logger.info(f"[hybrid_retrieve] SQL 旁路检索命中 {len(sql_docs)} 条结构化数据")
    except Exception as e:
        logger.warning(f"[hybrid_retrieve] SQL 旁路检索失败，降级纯 RAG: {e}")

    # 并行执行：Vector 和 BM25 互不依赖（共享线程池）
    from backend.infra.thread_pools import retrieval_pool_inner
    ex = retrieval_pool_inner()
    failures: dict[str, BaseException] = {}
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

    # SQL 旁路检索结果参与 RRF 融合（精确匹配，给高权重）
    for rank, doc in enumerate(sql_docs, start=1):
        cid = doc.metadata.get("chunk_id") or _fallback_id(doc)
        rank_map[cid] = rank_map.get(cid, 0) + 1 / (rrf_k + rank)

    sorted_cids = sorted(rank_map.items(), key=lambda x: x[1], reverse=True)

    doc_dict = {doc.metadata.get("chunk_id") or _fallback_id(doc): doc for doc in vector_docs + bm25_docs + sql_docs}

    merged = []
    for cid, rrf_score in sorted_cids[:k]:
        doc = doc_dict[cid]
        doc.metadata["rrf_score"] = round(rrf_score, 4)
        doc.metadata["retrieval_mode"] = "hybrid"
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
               "merged_hits": len(merged),
               "query_tier": query_tier}
    if sql_bypass_used:
        metrics["sql_bypass_hits"] = len(sql_docs)
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
            decision = _evaluate_retrieval_gate(merged, query)
            merged[0].metadata["__evidence_gate_decision__"] = decision.to_metrics()
        except Exception as e:
            logger.warning(f"[hybrid_retrieve] evidence_gate 评估异常: {e}")

    return merged


# ── 财务 SQL 旁路检索 ──────────────────────────────────


def _financial_sql_bypass(query: str) -> list:
    """财务 SQL 旁路检索：查询含财务指标 + 数值条件时，走 SQL 精确检索。

    流程：
      1. QueryAnalyzer 分析查询 → 提取 financial_metrics + numeric_conditions
      2. 仅当同时有指标和数值条件时才触发（避免对纯文本查询误走 SQL）
      3. 构造安全 SELECT SQL，查询已入库的结构化财务数据表
      4. SQL 结果转为 Document 对象，注入 RRF 融合

    失败软降级：SQL 执行失败返回空列表，不影响 Vector+BM25 检索。
    """
    from backend.shared.logger import logger

    try:
        from backend.rag.retrieval.query_analyzer import QueryAnalyzer
    except ImportError:
        return []

    parsed = QueryAnalyzer().analyze(query)
    # 仅当同时有财务指标和数值条件时才触发
    if not parsed.financial_metrics or not parsed.numeric_conditions:
        return []

    # 构造 SQL 查询
    sql = _build_financial_sql(parsed)
    if not sql:
        return []

    logger.info(f"[SQL_Bypass] 查询: {query[:80]}, SQL: {sql[:120]}")

    try:
        from backend.sql.sql_validator import sql_validator
        safe_sql, _, _ = sql_validator.validate(sql)
        from backend.sql.executor import execute_sql_struct
        from backend.sql.schema_loader import schema_loader
        result = execute_sql_struct(safe_sql, timeout=schema_loader.query_timeout)
        if result.status not in ("success", "no_data"):
            logger.warning(f"[SQL_Bypass] SQL 执行失败: {result.status} - {result.error}")
            return []
        return _sql_results_to_documents(result, parsed)
    except Exception as e:
        logger.warning(f"[SQL_Bypass] SQL 旁路检索失败: {e}")
        return []


def _build_financial_sql(parsed) -> str:
    """根据解析结果构造安全 SELECT SQL。

    策略：查询已入库的财务数据表，按 numeric_conditions 过滤。
    使用指标文本做 ILIKE 匹配列值，避免硬编码列名。
    表名由 schema_loader 动态发现，兑底用 stg_financial_data。
    """
    # 为每个 numeric_condition 构造 WHERE 子句
    where_clauses: list[str] = []
    for cond in parsed.numeric_conditions:
        metric = cond.get("metric_text", "")
        value = cond.get("value", 0)
        if not metric or not isinstance(value, (int, float)):
            continue
        # 指标文本可能在任意列中，用 ILIKE 跨列匹配
        # 简化：在已知财务表中按指标名做列值匹配
        where_clauses.append(
            f"EXISTS (SELECT 1 FROM unnest(string_to_array(::text, ',')) AS col_val "
            f"WHERE col_val ILIKE '%{metric}%')"
        )
        where_clauses.append(f"{value} IS NOT NULL")
        break  # 只用第一个条件，避免多条件冲突

    if not where_clauses:
        return ""

    # 动态查找含财务指标的表名，兑底用 stg_financial_data
    table_name = "stg_financial_data"
    try:
        from backend.sql.schema_loader import schema_loader
        known_tables = getattr(schema_loader, "tables", []) or []
        for t in known_tables:
            tname = str(getattr(t, "table_name", "")) or str(t)
            if "financial" in tname.lower() or "finance" in tname.lower():
                table_name = tname
                break
    except Exception:
        logger.debug("[P1-10] 财务表探测失败（降级默认表名）", exc_info=True)

    conditions = " AND ".join(where_clauses)
    sql = f"SELECT * FROM {table_name} WHERE {conditions} LIMIT 20"
    return sql


def _sql_results_to_documents(result, parsed) -> list:
    """将 SQL 查询结果转为 Document 列表。

    每行结果转成一个 Document，page_content 为 kv 格式文本，
    metadata 含 numeric_values 和来源标记。
    """
    import hashlib

    from langchain_core.documents import Document

    docs: list = []
    columns = result.columns or []
    rows = result.rows or []

    for ri, row in enumerate(rows):
        # 构建 kv 文本
        kv_parts: list[str] = ["[SQL财务数据]"]
        numeric_vals: dict[str, str] = {}
        for ci, col in enumerate(columns):
            if ci >= len(row):
                continue
            val = str(row[ci]) if row[ci] is not None else ""
            col_name = str(col).strip()
            if not col_name or not val:
                continue
            kv_parts.append(f"{col_name} {val}")
            # 尝试提取数值
            try:
                from backend.rag.preprocessing.financial_normalizer import normalize_financial_value
                norm = normalize_financial_value(val)
                if norm.is_numeric and norm.normalized is not None:
                    numeric_vals[col_name] = norm.normalized
            except Exception:
                logger.debug("[P1-10] 财务数值规范化失败", exc_info=True)

        text = "\n".join(kv_parts)
        chunk_id = hashlib.md5(f"sql:{ri}:{text[:100]}".encode()).hexdigest()[:12]
        meta = {
            "chunk_id": chunk_id,
            "doc_id": f"sql_bypass:{chunk_id}",
            "granularity": "leaf",
            "source_file": "sql_bypass",
            "chunk_type": "sql_result",
            "row_index": ri,
            "financial_metrics": parsed.financial_metrics,
            "chunk_tokens": 0,  # 由下游填充
        }
        if numeric_vals:
            meta["numeric_values"] = numeric_vals
        docs.append(Document(page_content=text, metadata=meta))

    return docs
