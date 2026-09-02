from backend.shared.logger import logger

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
    """增强版混合检索 - 自动启用三路召回（Rule + Dense + Sparse）

    P2 优化集成点：
      - 从 enhanced_hybrid_retrieval 导入核心逻辑
      - 当 ADAPTIVE_THRESHOLD_ENABLED=true 时启用动态阈值
      - Rule-based retriever 仅对 FAQ/条款类查询激活
    """
    from backend.config.rag import ADAPTIVE_THRESHOLD_ENABLED, CONFIDENCE_AGGREGATOR_ENABLED
    
    # 尝试启用增强检索（如果配置开启且依赖可用）
    if ADAPTIVE_THRESHOLD_ENABLED and CONFIDENCE_AGGREGATOR_ENABLED:
        try:
            logger.info(f"[hybrid_retrieve] Using ENHANCED multi-path retrieval for query='{query[:50]}...'")
            from backend.rag.retrieval.enhanced_hybrid_retrieval import (
                enhanced_hybrid_retrieve as enhanced_retrieve,
                ConfidenceAggregator,
            )
            
            confidence_aggregator = ConfidenceAggregator() if CONFIDENCE_AGGREGATOR_ENABLED else None
            docs, meta = enhanced_retrieve(
                query, vector_retriever, bm25_retriever,
                k=k, doc_ids=doc_ids, rrf_k=rrf_k, metadata_filter=metadata_filter,
                expanded_queries=expanded_queries,
                confidence_aggregator=confidence_aggregator,
            )
            
            # 将 confidence 信息注入 metadata 供下游使用
            for i, doc in enumerate(docs[:3]):
                doc.metadata[f"enhanced_confidence_{i}"] = meta.get("confidence_score", 0)
            
            return docs
            
        except ImportError as e:
            logger.warning(f"[hybrid_retrieve] Enhanced retrieval import failed ({e}), fallback to original")
        except Exception as e:
            logger.warning(f"[hybrid_retrieve] Enhanced retrieval error ({e}), falling back to original")
    
    # Fallback: 原始 hybrid 逻辑
    logger.info(f"[hybrid_retrieve] Using ORIGINAL retrieval (adaptive disabled or fallback)")
    from backend.observability.tracer import SpanName, trace_collector
    span = trace_collector.start_span("hybrid_retrieval", name=SpanName.RETRIEVAL)

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

    # SQL 旁路检索结果参与 RRF 融合（精确匹配，给高权重）
    for rank, doc in enumerate(sql_docs, start=1):
        cid = doc.metadata.get("chunk_id") or _fallback_id(doc)
        rank_map[cid] = rank_map.get(cid, 0) + 1 / (rrf_k + rank)

    sorted_cids = sorted(rank_map.items(), key=lambda x: x[1], reverse=True)

    doc_dict = {doc.metadata.get("chunk_id") or _fallback_id(doc): doc for doc in vector_docs + bm25_docs + sql_docs}

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
            from backend.rag.evidence_gate import (
                evidence_gate_retrieval,
                gate_retrieval_passthrough,
                is_evidence_gate_enabled,
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
                    DOC_TYPE_COVERAGE_REQUIRED,
                    VEC_MIN_SCORE,
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
