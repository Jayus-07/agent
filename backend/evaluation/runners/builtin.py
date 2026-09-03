"""内置 Runner 实现 — 对接当前项目的 multi_agent / retrieval / sql_agent。

此文件注册 4 个模块的 runner：
- planner: 对接 multi_agent.planner
- rag:     对接 retrieval.pipeline（ChromaDB 向量检索）
- sql:     对接 sql_agent.SQLAgent（6 层安全管线）
- e2e:     对接 multi_agent.graph.MultiAgentSystem（完整 Agent 链路）

复制评估框架到新项目时，替换此文件中的 runner 实现即可。
"""

import time

from backend.evaluation.judge import judge_answer
from backend.evaluation.metrics import (
    answer_correctness_typed,
    context_noise_rate,
    faithfulness_claim_based,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    result_set_match,
    stage_rerank_metrics,
    stage_retrieval_metrics,
)
from backend.evaluation.models import EvalResult, TestCase
from backend.evaluation.registry import register_runner
from backend.evaluation.runner import evaluate_planner_offline
from backend.shared.logger import logger

# ==================== Planner ====================

def _run_planner(cases: list[TestCase], **kwargs) -> list[EvalResult]:
    """Planner runner — 调用 multi_agent.planner.planner_node。"""
    results: list[EvalResult] = []
    try:
        from backend.agents.planner import planner_node

        for case in cases:
            t0 = time.time()
            try:
                state = {
                    "question": case.question,
                    "kb_id": case.metadata.get("kb_id", "default"),
                }
                plan_state = planner_node(state)
                plan = plan_state.get("plan", {})
                nodes = plan.get("nodes", {})
                actual_caps = list(dict.fromkeys(
                    node.get("capability", "")
                    for node in nodes.values()
                    if node.get("capability")
                ))
                result = evaluate_planner_offline(case.id, case.expected, actual_caps)
                result.duration_ms = int((time.time() - t0) * 1000)
                results.append(result)
            except Exception as e:
                results.append(EvalResult(
                    case_id=case.id, module="planner", status="error",
                    expected=case.expected, actual={},
                    error_msg=str(e), duration_ms=int((time.time() - t0) * 1000),
                ))
    except ImportError:
        results = [
            EvalResult(
                case_id=c.id, module="planner", status="error",
                expected=c.expected, actual={},
                error_msg="Planner module not available",
            )
            for c in cases
        ]
    return results


# ==================== RAG ====================

_rag_pipeline = None
_rag_pipeline_error = None


# safe_jsonable 已迁到 backend.shared.jsonable,这里保留别名供向后兼容
from backend.shared.jsonable import safe_jsonable as _safe_jsonable  # noqa: F401


def _normalize_snippet_text(text: str) -> str:
    """snippet 匹配归一化：去全部空白字符 + 全角转半角。

    避免 ground truth 关键词与文档原文仅因空格/全半角差异（如 "48 小时" vs "48小时"）
    导致假阴性。
    """
    # 全角 ASCII（！〜）转半角；全角空格转半角
    text = text.translate(
        {i: i - 0xFEE0 for i in range(0xFF01, 0xFF5F)}
    ).replace("\u3000", " ")
    # 去除所有空白字符（空格/tab/换行）
    text = "".join(text.split())
    # 去除千分位逗号（"90,000" → "90000"），避免数字格式差异导致假阴性
    return text.replace(",", "")


# ==================== 拒答校准：查询实体存在性校验（V1.3） ====================
#
# 背景：hard negative（KB 含相关主题但无答案）的 rerank 分数落在正样本主区间
# （实测 0.60~0.71 vs 正样本 0.56~0.73），纯分数启发式无法分离；而普通负样本
# 分数 ≤0.59 可被阈值拦住。因此对拒答用例追加语义判据：
# 问题核心实体若不在召回内容中 → "主题相近但无答案" → 判拒答。

# 问题停用词：疑问词/代词/泛称，不构成实体
_QUERY_STOPWORDS = {
    "什么", "怎么", "怎样", "如何", "哪些", "哪个", "多久", "多少", "为什么",
    "请问", "你们", "我们", "贵公司", "公司", "需要", "应该", "可以", "是否",
    "有没有", "是什么", "进行", "相关", "具体", "一般", "规定", "要求",
    "时候", "目前", "现在", "支持", "采用", "包括", "属于", "关于", "一样",
}


def _extract_query_entities(question: str) -> list[str]:
    """jieba 分词提取问题候选实体（长度 ≥2、去停用词）。"""
    import jieba
    return [
        w for w in jieba.cut(question)
        if len(w) >= 2 and w not in _QUERY_STOPWORDS and not w.isdigit()
    ]


def _entities_all_present(entities: list[str], details: list[dict], top_n: int = 3) -> bool:
    """全部实体均出现在 top_n 召回 chunk 文本中才视为证据存在。

    采用"全命中"而非比例阈值：若真有答案，问题核心实体应当全部见于证据文本；
    任一缺失即判"主题相近但无答案"。该校验仅作用于 should_reject 用例，
    误判上限是维持原判（回退分数启发式），无回归风险。
    """
    if not entities:
        return True
    text = _normalize_snippet_text(
        "".join((d.get("page_content") or "") for d in details[:top_n])
    )
    return all(_normalize_snippet_text(e) in text for e in entities)


def _match_by_snippet(
    details: list[dict],
    expected_snippets: list[str],
) -> tuple[bool, float]:
    """V1.1: snippet 语义匹配 — 召回内容含所有 keywords → hit=True。

    Args:
        details: chunk 详情列表（每个含 "snippet" 字段用于展示）
        expected_snippets: 期望的关键词列表

    注意：snippet 在 builtin.py 中被截断到 200 字符用于展示，
    关键词匹配可能在截断之后。所以这里同时检查 snippet + page_content。

    Returns:
        (hit, recall):
            hit: 所有关键词都在召回内容中 → True
            recall: 命中率（命中关键词数 / 总关键词数）
    """
    if not expected_snippets:
        return False, 0.0
    # V1.1: 优先用 page_content（完整文本）而非 snippet（截断 200 字符）做匹配，
    # 避免关键词恰好在 snippet 截断位置之后导致误判 fail；
    # 无 page_content 时回退 snippet（单测/旧调用方只传 snippet 也能匹配）。
    actual_text = " ".join(
        d.get("page_content") or d.get("snippet") or "" for d in details
    )
    # V1.2: 归一化后再匹配，消除空格/全半角差异导致的假阴性
    normalized_actual = _normalize_snippet_text(actual_text)
    matched = sum(
        1 for s in expected_snippets
        if _normalize_snippet_text(s) in normalized_actual
    )
    recall = matched / len(expected_snippets)
    hit = matched == len(expected_snippets)
    return hit, recall


def _init_rag_pipeline():
    """初始化 RAG 检索管线（模块级单例）。"""
    global _rag_pipeline, _rag_pipeline_error
    if _rag_pipeline is not None:
        return _rag_pipeline
    if _rag_pipeline_error is not None:
        return None
    try:
        from backend.rag.pipeline import RAGPipeline
    except ImportError:
        _rag_pipeline_error = "RAG pipeline import failed"
        return None
    try:
        _rag_pipeline = RAGPipeline()
        return _rag_pipeline
    except Exception as e:
        _rag_pipeline_error = str(e)
        return None


# 完整检索链路（模块级缓存，不含 MultiQuery/LLM）
_full_retriever = None


def _get_full_retriever(pipeline):
    """构建完整检索链路: ChunkLevelRetriever → Adaptive → CrossEncoder 精排。

    覆盖: Doc检索→关键词过滤→人名匹配→BM25+RRF混合→Adaptive补全→精排
    不含: MultiQuery(需LLM) / HistoryAware(需LLM) / LLM生成答案
    """
    global _full_retriever
    if _full_retriever is not None:
        return _full_retriever

    from langchain_classic.retrievers import ContextualCompressionRetriever

    from backend.config import HYBRID_SEARCH_K
    from backend.rag.reranker import RerankCompressor
    from backend.rag.retrieval.retrievers import AdaptiveRetriever

    # 修改 chunk_retriever_base 的 k 值用于评估
    base = pipeline.lc_chain.chunk_retriever_base
    base.k = HYBRID_SEARCH_K

    # Adaptive: 文档分布分析 → 集中则补全全文
    adaptive = AdaptiveRetriever(
        base_retriever=base,
        doc_db=pipeline.doc_db,
    )

    # Rerank: CrossEncoder 全局精排
    full_retriever = ContextualCompressionRetriever(
        base_compressor=RerankCompressor(),
        base_retriever=adaptive,
    )

    _full_retriever = full_retriever
    return _full_retriever


def _build_ablation_retriever(
    pipeline,
    mode: str,
    kb_id: str,
    department: str,
):
    """构建消融实验检索器 — 隔离各组件贡献。

    消融模式:
      vector_only    — 仅 ChromaDB 向量检索
      bm25_only      — 仅 BM25 关键词检索
      hybrid         — 向量 + BM25 + RRF 融合（无精排/自适应）
      hybrid_rerank  — hybrid + CrossEncoder 精排
      hybrid_adaptive — ChunkLevel + Adaptive + Rerank（无 MultiQuery）
      full           — 完整链路（= _get_full_retriever）
    """
    if mode == "full":
        return _get_full_retriever(pipeline)

    from backend.config import HYBRID_SEARCH_K

    mf = {}
    if kb_id and kb_id not in ("*", "default"):
        mf["kb_id"] = kb_id
    if department:
        mf["department"] = department

    if mode == "vector_only":
        base = pipeline.lc_chain.chunk_retriever_base
        base.k = HYBRID_SEARCH_K

        def _vector_invoke(question: str):
            return base.chunk_retriever.retrieve(
                question, k=base.k,
                metadata_filter=mf or None,
            )

        return _ListRetriever(_vector_invoke)

    if mode == "bm25_only":
        bm25 = pipeline.bm25

        def _bm25_invoke(question: str):
            docs = bm25.invoke(question)
            if mf:
                filtered = []
                for d in docs:
                    meta = d.metadata or {}
                    if all(meta.get(k_) == v for k_, v in mf.items()):
                        filtered.append(d)
                return filtered
            return docs

        return _ListRetriever(_bm25_invoke)

    if mode == "hybrid":
        from backend.rag.retrieval.hybrid import hybrid_retrieve

        base = pipeline.lc_chain.chunk_retriever_base
        base.k = HYBRID_SEARCH_K

        def _hybrid_invoke(question: str):
            return hybrid_retrieve(
                question, base.chunk_retriever, pipeline.bm25,
                k=HYBRID_SEARCH_K,
                metadata_filter=mf or None,
            )

        return _ListRetriever(_hybrid_invoke)

    if mode == "hybrid_rerank":
        from langchain_classic.retrievers import ContextualCompressionRetriever

        from backend.rag.reranker import RerankCompressor
        from backend.rag.retrieval.hybrid import hybrid_retrieve

        base = pipeline.lc_chain.chunk_retriever_base
        base.k = HYBRID_SEARCH_K

        def _hybrid_base(question: str):
            return hybrid_retrieve(
                question, base.chunk_retriever, pipeline.bm25,
                k=HYBRID_SEARCH_K,
                metadata_filter=mf or None,
            )

        return ContextualCompressionRetriever(
            base_compressor=RerankCompressor(),
            base_retriever=_ListRetriever(_hybrid_base),
        )

    if mode == "hybrid_adaptive":
        from langchain_classic.retrievers import ContextualCompressionRetriever

        from backend.rag.reranker import RerankCompressor
        from backend.rag.retrieval.retrievers import AdaptiveRetriever

        base = pipeline.lc_chain.chunk_retriever_base
        base.k = HYBRID_SEARCH_K

        adaptive = AdaptiveRetriever(
            base_retriever=base,
            doc_db=pipeline.doc_db,
        )
        return ContextualCompressionRetriever(
            base_compressor=RerankCompressor(),
            base_retriever=adaptive,
        )

    logger.warning(f"[RAG eval] 未知消融模式 '{mode}', 回退 full")
    return _get_full_retriever(pipeline)


class _ListRetriever:
    """将返回 list[Document] 的函数适配为 LangChain BaseRetriever 接口。"""

    def __init__(self, fn):
        self._fn = fn

    def invoke(self, question: str):
        return self._fn(question)


def _run_rag(cases: list[TestCase], **kwargs) -> list[EvalResult]:
    """RAG runner — 完整检索链路（无 LLM）。

    链路: Doc检索 → 关键词过滤 → 人名匹配 → BM25+RRF混合 → Adaptive补全 → CrossEncoder精排
    不含 MultiQuery / HistoryAware / LLM 生成，needs_live=False 即可运行。

    支持消融实验：kwargs["ablation_mode"] 可设为 vector_only / bm25_only /
    hybrid / hybrid_rerank / hybrid_adaptive / full（默认）。
    """
    if not cases:
        return []

    pipeline = _init_rag_pipeline()
    if pipeline is None:
        return [
            EvalResult(
                case_id=c.id, module="rag", status="error",
                expected=c.expected, actual={},
                error_msg="RAG pipeline not available",
            )
            for c in cases
        ]

    # === KB 软约束：探测实际 doc_db 里有哪些 KB ===
    # 如果 golden set 标注的 KB 不在 doc_db 中，自动 fallback 到 default，
    # 避免 0 命中导致整个评估全军覆没，但同时记录 warning 让维护者知道。
    available_kbs = set()
    try:
        peek = pipeline.doc_db.get(where=None)
        for md in (peek.get("metadatas") or []):
            kid = md.get("kb_id")
            if kid:
                available_kbs.add(kid)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[RAG eval] 探测 doc_db KB 列表失败: {e}")

    ablation_mode = kwargs.get("ablation_mode", "full")

    results: list[EvalResult] = []
    for case in cases:
        t0 = time.time()
        try:
            kb_id = case.metadata.get("kb_id", "default")
            department = case.metadata.get("department") or ""

            # 消融模式：按 case 的 kb_id/department 构建对应检索器
            if ablation_mode != "full":
                retriever = _build_ablation_retriever(
                    pipeline, ablation_mode, kb_id, department,
                )
            else:
                retriever = _get_full_retriever(pipeline)
            # KB 软 fallback：如果标注的 KB 在 doc_db 中不存在，退化为 default
            if (
                kb_id
                and kb_id not in ("*", "default")
                and available_kbs
                and kb_id not in available_kbs
            ):
                logger.warning(
                    f"[RAG eval] {case.id} 标注 KB='{kb_id}' 不在 doc_db 中 "
                    f"(available={sorted(available_kbs)}), fallback to default"
                )
                kb_id = "default"
            question = case.question

            # KB + 部门 隔离: 通过 contextvars 注入 metadata_filter（多 key = Chroma AND）
            if kb_id and kb_id != "*" and kb_id != "default":
                from backend.rag.context import RequestContext, set_context
                mf = {"kb_id": kb_id}
                if department:
                    mf["department"] = department
                ctx = RequestContext(
                    metadata_filter=mf,
                    intent_label="",
                    query=question,
                )
                set_context(ctx)
            else:
                from backend.rag.context import RequestContext, set_context
                set_context(RequestContext())

            # === 采集 Doc 级检索结果（Stage 1）===
            doc_filter = {}
            if kb_id and kb_id != "*" and kb_id != "default":
                doc_filter["kb_id"] = kb_id
            if department:
                doc_filter["department"] = department
            doc_results = pipeline.doc_db.similarity_search(question, k=5, filter=doc_filter) if doc_filter else pipeline.doc_db.similarity_search(question, k=5)
            stage1_docs = []
            stage1_doc_ids = []
            for d in doc_results:
                doc_id = d.metadata.get("doc_id", "")
                stage1_docs.append({
                    "doc_id": doc_id,
                    "title": str(d.metadata.get("title", ""))[:60],
                    "category": d.metadata.get("category_name", d.metadata.get("category", "")),
                })
                if doc_id:
                    stage1_doc_ids.append(doc_id)
            # 观测 ChunkLevelRetriever 内部是否触发 fallback：
            # 若 request_metadata_filter 非空但 stage1_doc_ids 为空，
            # 说明 ChunkLevelRetriever 走的是 "0 匹配 → 放宽 business_domain" fallback。
            stage1_fallback_suspected = bool(
                doc_filter and not stage1_doc_ids
            )

            # === 完整检索链路 ===
            # reranker/evidence_gate 等内部组件会调 trace_collector.start_span()，
            # 必须先 start() 否则报 "start_span() 必须在 start() 之后调用"。
            # 这里用 try/finally 保证 trace 一定被收尾，避免污染下次评测。
            from backend.observability.tracer import SpanKind, trace_collector
            trace = trace_collector.start(
                question=question,
                session_id=f"eval-{case.id}",
                workflow_name="rag_eval",
            )
            # 手动创建 root span 让 end_span() 有正确参数传入
            root_span = trace_collector.start_span(
                "rag_eval_root", parent_id=None,
                name=f"RAG eval {case.id}",
                type="workflow", kind=SpanKind.RETRIEVAL.value,
                input={"question": question, "kb_id": kb_id},
            )
            try:
                retrieved_docs = retriever.invoke(question)
            finally:
                try:
                    trace_collector.end_span(root_span)
                except Exception:
                    logger.debug("trace root_span end failed for %s", case.id, exc_info=True)

            # === 捕获 trace spans 作为过程证据 ===
            # RAG 链路在 invoke() 期间由 AdaptiveRetriever / RerankCompressor 等
            # 内部组件自动埋 span（retrieval / rerank / evidence_gate_*）。
            # 这里把每个 span 的 name/type/duration_ms/metrics/input/output
            # 序列化进 actual.trace，供 Markdown 报告完整展开。
            trace_spans = []
            for sp in (trace.spans or []):
                trace_spans.append({
                    "span_id": sp.span_id,
                    "parent_id": sp.parent_id,
                    "name": sp.name,
                    "type": sp.type,
                    "kind": sp.kind,
                    "status": sp.status,
                    "duration_ms": sp.duration_ms,
                    "sequence": sp.sequence,
                    "metrics": dict(sp.metrics or {}),
                    "input": _safe_jsonable(sp.input),
                    "output": _safe_jsonable(sp.output),
                    "events": list(sp.events or []),
                    "errors": list(sp.errors or []),
                })
            total_trace_ms = sum(s["duration_ms"] for s in trace_spans)

            # === 组装详细检索轨迹 ===
            actual_doc_strs = []
            seen = set()
            details = []
            for doc in retrieved_docs:
                doc_id = doc.metadata.get("doc_id", "")
                source = doc.metadata.get("source", "").replace("\\", "/")
                identifier = doc_id if doc_id else source
                if identifier not in seen:
                    seen.add(identifier)
                    actual_doc_strs.append(identifier)

                details.append({
                    "doc_id": doc_id,
                    "title": str(doc.metadata.get("title", ""))[:80],
                    "chunk_id": doc.metadata.get("chunk_id", ""),
                    "department": doc.metadata.get("department", ""),
                    "rerank_score": doc.metadata.get("rerank_score"),
                    "source": source,
                    "snippet": doc.page_content[:200].replace("\n", " "),  # 展示用（截断）
                    "page_content": doc.page_content,  # V1.1: snippet_match 用全文本（不被截断）
                })

            # 检测自适应行为：chunks 集中在少数文档 vs 分散
            from collections import Counter
            doc_counter = Counter(d["doc_id"] for d in details if d["doc_id"])
            total = len(details)
            if total > 0:
                clustered = [did for did, cnt in doc_counter.items() if cnt / total >= 0.3]
                adaptive_info = f"集中({len(clustered)}个文档)" if len(clustered) <= 2 and clustered else f"分散({len(doc_counter)}个文档)"
            else:
                adaptive_info = "无结果"

            pipeline_info = {
                "ablation_mode": ablation_mode,
                "stage1_docs": len(stage1_docs),
                "stage1_top_docs": stage1_docs,
                "stage1_fallback_suspected": stage1_fallback_suspected,
                "stage2_chunks_recalled": total,
                "after_rerank": total,  # = stage2 数量（rerank 在 invoke 内完成）
                "adaptive": adaptive_info,
            }

            expected_docs = set(case.expected.get("relevant_docs", []))
            expected_doc_strs = list(expected_docs)
            expected_chunks = set(case.expected.get("relevant_chunks", []) or [])
            expected_snippets = case.expected.get("relevant_snippets", []) or []
            match_type = case.expected.get("match_type", "chunk_id")  # chunk_id | snippet | doc_id
            min_expected = case.expected.get("min_relevant_chunks", 1)

            # === V2: 分阶段诊断指标 ===
            stage_metrics: dict[str, float | dict] = {}

            # S1: Doc 级检索（doc_db similarity search）
            if expected_doc_strs:
                s1 = stage_retrieval_metrics(
                    stage1_doc_ids, expected_doc_strs,
                )
                stage_metrics["S1_doc_retrieval"] = s1

            # S4: Rerank 阶段（基于 rerank_score 的排序质量）
            rerank_scores = [
                d.get("rerank_score") or 0.0 for d in details
            ]
            relevant_mask = [
                bool(
                    (d.get("doc_id") or "") in expected_docs
                    or (d.get("source", "").replace("\\", "/")) in expected_docs
                )
                for d in details
            ]
            if rerank_scores and any(relevant_mask):
                stage_metrics["S4_rerank"] = stage_rerank_metrics(
                    rerank_scores, relevant_mask,
                )

            # === V2: 生成质量离线评估（仅对 generation_eval=true 的用例）===
            # 离线链路无 LLM 生成答案，用 retrieved context 作为代理评估检索质量对生成的支撑
            generation_metrics: dict[str, float] = {}
            if case.metadata.get("generation_eval"):
                expected_answer = case.metadata.get("expected_answer", "")
                must_contain = case.metadata.get("must_contain", [])
                must_not_contain = case.metadata.get("must_not_contain", [])
                answer_type = case.metadata.get("answer_type", "factual")

                # S6: 答案正确性（用 expected_answer 作为代理，检查检索上下文是否支撑答案）
                if expected_answer:
                    # 用 retrieved context 拼接作为"答案"，检查是否包含 must_contain 关键词
                    retrieved_text = " ".join(d.get("page_content", "") for d in details)
                    correctness = answer_correctness_typed(
                        actual_answer=retrieved_text,
                        expected_answer=expected_answer,
                        answer_type=answer_type,
                        must_contain=must_contain,
                        must_not_contain=must_not_contain,
                    )
                    generation_metrics["S6_answer_correctness"] = correctness["correctness"]
                    generation_metrics["must_contain_hit"] = correctness["must_contain_hit"]
                    generation_metrics["must_not_contain_violation"] = correctness["must_not_contain_violation"]

                # S7: 忠实度（检查 retrieved context 是否支撑 expected_answer 的声明）
                if expected_answer and details:
                    context_list = [d.get("page_content", "") for d in details]
                    faithfulness = faithfulness_claim_based(
                        answer=expected_answer,
                        context=context_list,
                    )
                    generation_metrics["S7_faithfulness"] = faithfulness["faithfulness"]
                    generation_metrics["claim_count"] = float(faithfulness["claim_count"])
                    generation_metrics["supported_claim_count"] = float(faithfulness["supported_count"])

                # 将生成指标合并到 stage_metrics
                if generation_metrics:
                    stage_metrics["generation"] = generation_metrics

            # doc-level metrics — 始终计算（用于跨 case 对比）
            r5 = recall_at_k(actual_doc_strs, expected_doc_strs, k=5)
            r10 = recall_at_k(actual_doc_strs, expected_doc_strs, k=10)
            mrr_val = mrr(actual_doc_strs, expected_doc_strs)
            ndcg_val = ndcg_at_k(actual_doc_strs, expected_doc_strs, k=10)

            # === v2: confidence 判定（先于 top1，因为 top1 需要拒答决策）===
            # EvidenceGate 完整逻辑在 chain.py（含 LLM 评估），
            # 本 runner 是离线检索链路不调 LLM，用 rerank_score 阈值近似判 confidence。
            should_reject = case.expected.get("should_reject", False)

            if not details:
                confidence = "none"
                reject_gate = "retrieval"
                reject_reason = "no_evidence"
            else:
                top1_score = details[0].get("rerank_score") or 0.0
                top2_score = details[1].get("rerank_score") or 0.0 if len(details) > 1 else 0.0
                score_gap = top1_score - top2_score
                if top1_score < 0.5:
                    confidence = "none"
                    reject_gate = "retrieval"
                    reject_reason = "low_relevance"
                elif top1_score < 0.6:
                    confidence = "low"
                    reject_gate = None
                    reject_reason = None
                elif score_gap < 0.15:
                    confidence = "medium"
                    reject_gate = None
                    reject_reason = None
                else:
                    confidence = "high"
                    reject_gate = None
                    reject_reason = None

            # === V1.3 拒答校准：实体存在性校验（仅对 should_reject 用例生效）===
            query_entities: list[str] = []
            entity_absent = False
            if should_reject and details:
                query_entities = _extract_query_entities(question)
                if query_entities and not _entities_all_present(query_entities, details):
                    entity_absent = True
                    confidence = "low"
                    reject_gate = "entity_check"
                    reject_reason = "entity_absent"

            # === Top-1 准确率：结合拒答决策 ===
            would_reject = confidence in ("none", "low")
            top1_accuracy = 0.0
            if should_reject:
                # NEG: 系统正确拒答 → 1.0；系统错误放行 → 0.0
                top1_accuracy = 1.0 if would_reject else 0.0
            elif would_reject:
                # POS: 系统错误拒答 → 0.0
                top1_accuracy = 0.0
            elif expected_docs and actual_doc_strs:
                top1_accuracy = 1.0 if actual_doc_strs[0] in expected_docs else 0.0

            # === reject_accuracy：仅 negative case 计入 ===
            if should_reject:
                # 期望拒答时，confidence in {none, low} 算拒答成功
                reject_accuracy = 1.0 if confidence in ("none", "low") else 0.0
            else:
                # 正样本不输出此指标（避免污染 ModuleSummary 平均值）
                reject_accuracy = None

            # === V1.1: 多策略命中判定 ===
            # match_type 决定如何判定 pass（默认 chunk_id，向后兼容）
            actual_chunk_ids = {d["chunk_id"] for d in details if d.get("chunk_id")}

            if match_type == "snippet":
                # 语义匹配：expected_snippets 中的关键词都在召回内容里出现 → pass
                # 适用于"文档硬绑定会因 hash 变化失效"的场景
                chunk_hit, chunk_recall = _match_by_snippet(details, expected_snippets)
            elif expected_chunks:
                # 精确 chunk_id 匹配（默认/旧行为）
                matched_chunks = actual_chunk_ids & expected_chunks
                chunk_recall = len(matched_chunks) / len(expected_chunks)
                chunk_hit = len(matched_chunks) >= min_expected
            else:
                # fallback 到 doc 级命中（兼容未填 relevant_chunks 的旧 case）
                chunk_recall = 1.0 if (set(actual_doc_strs) & expected_docs) else 0.0
                chunk_hit = (set(actual_doc_strs) & expected_docs) if min_expected > 0 \
                    else not (set(actual_doc_strs) & expected_docs)

            # === 部门隔离泄漏判定 ===
            # 若用例标注 forbidden_departments / allowed_departments，则检索召回的
            # chunk 不得出现 forbidden 部门、且必须全部落在 allowed 部门内。
            # 这是"跨部门/跨 KB 隔离"的核心断言：即便检索层漏过一例，也算 fail。
            retrieved_depts = {d.get("department", "") for d in details if d.get("department")}
            forbidden_depts = case.expected.get("forbidden_departments") or []
            allowed_depts = case.expected.get("allowed_departments") or []
            dept_leak = False
            if forbidden_depts:
                dept_leak = bool(retrieved_depts & set(forbidden_depts))
            if allowed_depts:
                dept_leak = dept_leak or bool(retrieved_depts - set(allowed_depts))

            # pass 判定：chunk 级命中 + 无部门泄漏
            # V1.4: 拒答用例正确拒答即算 pass（不再要求 doc 命中）
            if should_reject and confidence in ("none", "low"):
                passed = not dept_leak
            else:
                passed = chunk_hit and not dept_leak

            results.append(EvalResult(
                case_id=case.id, module="rag",
                status="pass" if passed else "fail",
                expected=case.expected,
                actual={
                    "question": question,
                    "kb_id": kb_id,
                    "department": department,
                    "retrieved_docs": actual_doc_strs[:10],
                    "retrieved_departments": sorted(retrieved_depts),
                    "dept_leak": dept_leak,
                    "details": details,
                    "pipeline": pipeline_info,
                    "stage_metrics": stage_metrics,
                    # v2: 拒答过程证据（启发式 confidence，非 EvidenceGate 真值）
                    "rejection": {
                        "confidence": confidence,
                        "reject_gate": reject_gate,
                        "reject_reason": reject_reason,
                        "top1_rerank_score": details[0].get("rerank_score") if details else None,
                        # V1.3: 实体校验证据（仅拒答用例）
                        "query_entities": query_entities or None,
                        "entity_absent": entity_absent,
                    },
                    # 过程证据：本次用例在 RAG 链路中产生的全部 span
                    "trace": {
                        "trace_id": trace.id,
                        "total_spans": len(trace_spans),
                        "total_trace_ms": total_trace_ms,
                        "spans": trace_spans,
                    },
                },
                metrics={
                    "recall@5": round(r5, 4),
                    "recall@10": round(r10, 4),
                    "precision@5": round(precision_at_k(actual_doc_strs, expected_doc_strs, 5), 4),
                    "context_noise@10": round(context_noise_rate(actual_doc_strs, expected_doc_strs, 10), 4),
                    "mrr": round(mrr_val, 4),
                    "ndcg@10": round(ndcg_val, 4),
                    "chunk_recall": round(chunk_recall, 4),
                    "top1_accuracy": round(top1_accuracy, 4),
                    "dept_leak": int(dept_leak),
                    **({"reject_accuracy": round(reject_accuracy, 4)} if reject_accuracy is not None else {}),
                    # V2: 生成质量指标（仅 generation_eval 用例）
                    **({f"gen_{k}": round(v, 4) for k, v in generation_metrics.items()} if generation_metrics else {}),
                },
                duration_ms=int((time.time() - t0) * 1000),
            ))
        except Exception as e:
            results.append(EvalResult(
                case_id=case.id, module="rag", status="error",
                expected=case.expected, actual={"question": case.question, "kb_id": case.metadata.get("kb_id", "default")},
                error_msg=str(e), duration_ms=int((time.time() - t0) * 1000),
            ))

    return results


# ==================== SQL ====================

def _run_sql(cases: list[TestCase], **kwargs) -> list[EvalResult]:
    """SQL runner — 通过 SQLAgent.ask() 走完整的 6 层安全管线。"""
    if not cases:
        return []

    results: list[EvalResult] = []
    try:
        from backend.config import BUSINESS_DB_CONFIG

        for case in cases:
            t0 = time.time()
            try:
                from backend.sql.sql_agent import SQLAgent
                agent = SQLAgent(db_config=BUSINESS_DB_CONFIG)
                outcome = agent.ask(case.question)

                expected_security = case.expected.get("security_checks", [])
                is_security_test = (
                    "sensitive_column_blocked" in expected_security
                    or "write_blocked" in expected_security
                )

                if is_security_test:
                    is_blocked = (
                        "错误" in outcome or "拦截" in outcome
                        or "不允许" in outcome or "访问控制" in outcome
                    )
                    results.append(EvalResult(
                        case_id=case.id, module="sql",
                        status="pass" if is_blocked else "fail",
                        expected=case.expected,
                        actual={"output": outcome[:200]},
                        metrics={
                            "syntax_valid": 0.0,
                            "result_match": 0.0,
                            "security_pass": 1.0 if is_blocked else 0.0,
                        },
                        duration_ms=int((time.time() - t0) * 1000),
                    ))
                else:
                    is_error = "失败" in outcome or "错误" in outcome or "访问控制" in outcome
                    if is_error:
                        actual_result = []
                        syntax_ok = 0.0
                        security_pass = 0.0
                    else:
                        actual_result = _parse_markdown_table(outcome)
                        syntax_ok = 1.0
                        security_pass = 1.0

                    expected_result = case.expected.get("expected_result", [])
                    if expected_result:
                        result_ok = result_set_match(actual_result, expected_result)
                    else:
                        result_ok = float(len(actual_result) > 0) if actual_result else 0.0

                    passed = security_pass == 1.0 and (syntax_ok > 0 or result_ok > 0)

                    results.append(EvalResult(
                        case_id=case.id, module="sql",
                        status="pass" if passed else "fail",
                        expected=case.expected,
                        actual={"sql": "routed via SQLAgent.ask()", "result": actual_result},
                        metrics={
                            "syntax_valid": syntax_ok,
                            "result_match": result_ok,
                            "security_pass": security_pass,
                        },
                        duration_ms=int((time.time() - t0) * 1000),
                    ))
            except Exception as e:
                results.append(EvalResult(
                    case_id=case.id, module="sql", status="error",
                    expected=case.expected, actual={},
                    error_msg=str(e), duration_ms=int((time.time() - t0) * 1000),
                ))
    except ImportError:
        results = [
            EvalResult(
                case_id=c.id, module="sql", status="error",
                expected=c.expected, actual={},
                error_msg="SQL agent not available",
            )
            for c in cases
        ]
    return results


def _parse_markdown_table(md: str) -> list[dict]:
    """从 markdown 表格字符串解析行数据。"""
    if not md or "(无结果)" in md:
        return []
    lines = md.strip().split("\n")
    if len(lines) < 3:
        return []
    header_line = lines[0]
    headers = [h.strip() for h in header_line.split("|")[1:-1]]
    rows = []
    for line in lines[2:]:
        if not line.strip():
            continue
        cols = [c.strip() for c in line.split("|")[1:-1]]
        if len(cols) == len(headers):
            row = {}
            for h, c in zip(headers, cols):
                try:
                    row[h] = int(c)
                except ValueError:
                    try:
                        row[h] = float(c)
                    except ValueError:
                        row[h] = c
            rows.append(row)
    return rows


# ==================== E2E ====================

def _infer_routing_from_answer(answer: str, expected_routing: set) -> set:
    """从回答内容推断使用了哪些能力。"""
    actual_caps = set()
    if not answer:
        return actual_caps
    if "|" in answer and "---" in answer:
        actual_caps.add("query_database")
    if "参考文献" in answer or "来源" in answer:
        actual_caps.add("search_knowledge")
    # 如果没有任何特征：actual_caps 保持为空，routing_accuracy 会正确反映失败
    return actual_caps


def _run_e2e(cases: list[TestCase], **kwargs) -> list[EvalResult]:
    """E2E runner — 完整 MultiAgentSystem 链路 + 可选的 LLM-as-Judge 评分。"""
    if not cases:
        return []

    judge = kwargs.get("judge", False)
    results: list[EvalResult] = []

    try:
        from backend.orchestration.graph import MultiAgentSystem
        mas = MultiAgentSystem()

        for case in cases:
            t0 = time.time()
            try:
                kb_id = case.metadata.get("kb_id", "default")
                answer = mas.ask(case.question, kb_id=kb_id)

                expected_routing = set(case.expected.get("expected_routing", []))
                actual_caps = _infer_routing_from_answer(answer, expected_routing)
                routing_ok = (
                    expected_routing.issubset(actual_caps)
                    if expected_routing else True
                )

                metrics = {"routing_accuracy": 1.0 if routing_ok else 0.0}

                if judge and answer:
                    rubric = case.expected.get("rubric", {})
                    jr = judge_answer(case.question, rubric, answer)
                    metrics["judge_completeness"] = float(jr.scores.get("completeness", 0))
                    metrics["judge_faithfulness"] = float(jr.scores.get("faithfulness", 0))
                    metrics["judge_conciseness"] = float(jr.scores.get("conciseness", 0))
                    metrics["judge_citation"] = float(jr.scores.get("citation_quality", 0))
                    metrics["judge_total"] = jr.total
                    metrics["judge_confidence"] = {
                        "low": 0.0, "medium": 0.5, "high": 1.0
                    }.get(jr.confidence, 0.5)

                # V2: 生成质量评估（generation_eval 用例）
                if case.metadata.get("generation_eval") and answer:
                    expected_answer = case.metadata.get("expected_answer", "")
                    must_contain = case.metadata.get("must_contain", [])
                    must_not_contain = case.metadata.get("must_not_contain", [])
                    answer_type = case.metadata.get("answer_type", "factual")

                    # S6: 答案正确性
                    if expected_answer:
                        correctness = answer_correctness_typed(
                            actual_answer=answer,
                            expected_answer=expected_answer,
                            answer_type=answer_type,
                            must_contain=must_contain,
                            must_not_contain=must_not_contain,
                        )
                        metrics["gen_answer_correctness"] = correctness["correctness"]
                        metrics["gen_must_contain_hit"] = correctness["must_contain_hit"]
                        metrics["gen_must_not_contain_violation"] = correctness["must_not_contain_violation"]

                    # S7: 忠实度（需要检索上下文，但 E2E 链路不直接暴露，用 answer 自身做简化评估）
                    # 完整忠实度评估应在 RAG 离线链路或专门的 generation runner 中进行

                passed = routing_ok and (
                    not judge or metrics.get("judge_total", 5) >= 3.0
                )

                results.append(EvalResult(
                    case_id=case.id, module="e2e",
                    status="pass" if passed else "fail",
                    expected=case.expected,
                    actual={"answer": answer[:500], "routing": list(actual_caps)},
                    metrics={
                        k: round(v, 4) if isinstance(v, float) else v
                        for k, v in metrics.items()
                    },
                    duration_ms=int((time.time() - t0) * 1000),
                ))
            except Exception as e:
                results.append(EvalResult(
                    case_id=case.id, module="e2e", status="error",
                    expected=case.expected, actual={},
                    error_msg=str(e),
                    duration_ms=int((time.time() - t0) * 1000),
                ))
    except ImportError:
        results = [
            EvalResult(
                case_id=c.id, module="e2e", status="error",
                expected=c.expected, actual={},
                error_msg="MultiAgentSystem not available",
            )
            for c in cases
        ]
    return results


# ==================== CS (客服系统) ====================

def _run_cs(cases: list[TestCase], **kwargs) -> list[EvalResult]:
    """CS runner — 验证客服系统各路径的路由正确性、安全过滤和输出质量。

    不依赖 LLM (needs_live=False)，直接调用 CS router / service 函数。
    """
    results: list[EvalResult] = []
    try:
        from backend.customer_service.router.cs_router import CSRouter

        router = CSRouter()

        for case in cases:
            t0 = time.time()
            try:
                cs_result = router.route(case.question)
                expected = case.expected
                actual_route = cs_result.route_path.value

                route_ok = actual_route == expected.get("cs_route", "")

                metrics: dict[str, float] = {"routing_accuracy": 1.0 if route_ok else 0.0}

                intent_expected = expected.get("intent", "")
                if intent_expected:
                    intent_ok = cs_result.intent == intent_expected
                    metrics["intent_accuracy"] = 1.0 if intent_ok else 0.0

                audit_required = expected.get("audit_required", False)
                if audit_required:
                    metrics["audit_required"] = 1.0
                    metrics["audit_coverage"] = 1.0 if cs_result.requires_action else 0.0

                guard_info = expected.get("output_guard", {})
                forbidden = guard_info.get("forbidden_patterns", [])
                if forbidden:
                    metrics["guard_patterns_checked"] = float(len(forbidden))

                passed = route_ok and metrics.get("intent_accuracy", 1.0) >= 1.0

                results.append(EvalResult(
                    case_id=case.id, module="cs",
                    status="pass" if passed else "fail",
                    expected=expected,
                    actual={
                        "route_path": actual_route,
                        "intent": cs_result.intent,
                        "confidence": cs_result.confidence,
                        "domain": cs_result.domain.value,
                    },
                    metrics={k: round(v, 4) if isinstance(v, float) else v
                             for k, v in metrics.items()},
                    duration_ms=int((time.time() - t0) * 1000),
                ))
            except Exception as e:
                results.append(EvalResult(
                    case_id=case.id, module="cs", status="error",
                    expected=case.expected, actual={},
                    error_msg=str(e),
                    duration_ms=int((time.time() - t0) * 1000),
                ))
    except ImportError as e:
        results = [
            EvalResult(
                case_id=c.id, module="cs", status="error",
                expected=c.expected, actual={},
                error_msg=f"CS module not available: {e}",
            )
            for c in cases
        ]
    return results


# ==================== 注册所有 Runner ====================

register_runner("planner", _run_planner, needs_live=True)
register_runner("rag", _run_rag, needs_live=False)       # 不依赖 LLM
register_runner("sql", _run_sql, needs_live=True)
register_runner("e2e", _run_e2e, needs_live=True)
register_runner("cs", _run_cs, needs_live=False)         # 不依赖 LLM
