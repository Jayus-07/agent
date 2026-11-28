"""RAG Chain 检索器与 LCEL 执行链构建器。"""
from __future__ import annotations

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from backend.infra.llm.proxy import emit_stream_delta, extract_chunk_reasoning, extract_chunk_text
from backend.rag.chain_modules.compat import (
    get_chain_context,
    get_chain_llm,
    get_enable_history_aware,
    get_enable_token_streaming,
    get_evidence_token_budget,
)
from backend.rag.chain_modules.metrics_helper import _build_retrieval_span_metrics
from backend.rag.chain_modules.prompts import (
    _build_contextualize_prompt,
    _build_document_prompt,
    _build_qa_prompt,
)
from backend.rag.chain_modules.streaming import _llm_stream
from backend.rag.reranker import RerankCompressor
from backend.rag.retrieval.retrievers import AdaptiveRetriever, ChunkLevelRetriever
from backend.shared.logger import logger


def build_retrievers(chain_instance) -> None:
    """Step A: 构建 ChunkLevelRetriever 实例。"""
    chain_instance.chunk_retriever_base = ChunkLevelRetriever(
        doc_db=chain_instance.doc_db,
        vectordb=chain_instance.vectordb,
        chunk_retriever=chain_instance.chunk_retriever,
        bm25=chain_instance.bm25,
        person_index=chain_instance.person_index,
    )


def build_rag_chains(chain_instance) -> None:
    """Step B: 构建完整的检索-生成链。

    执行顺序（外层先执行）:
      ① HistoryAware   — Query Understanding: 利用对话历史重写指代/省略
      ② Rerank         — CrossEncoder:      全局重排序 + 阈值过滤
      ③ MultiQuery     — Query Expansion:   关键词检测复杂度 → LLM 多角度改写
      ④ Adaptive       — Document Expansion: 同文档相邻 Chunk 扩展
      ⑤ ChunkLevel     — Hybrid Retrieval:  向量 + BM25 混合检索
      ⑥ LLM Generate   — 带引用标注 [1][2] 的最终回答
    """
    from langchain_classic.chains import (
        create_history_aware_retriever,
        create_retrieval_chain,
    )
    from langchain_classic.chains.combine_documents import (
        create_stuff_documents_chain,
    )

    chain_instance._runtime_prompt_keys = ["rag.qa", "rag.document"]

    def _index_docs(input_dict):
        docs = input_dict.get("context", [])
        # ── 文档级权限过滤 ──
        try:
            from backend.rag.permissions import filter_documents_by_permission
            try:
                from backend.observability.metrics import (
                    rag_permission_filtered_total,
                )
            except Exception:  # noqa: BLE001
                class _Nop:
                    def inc(self, _n=1):
                        pass
                rag_permission_filtered_total = _Nop()
            _user_perms = get_chain_context().identity.permissions
            _allowed = filter_documents_by_permission(docs, _user_perms)
            _denied = len(docs) - len(_allowed)
            if _denied:
                logger.info(
                    f"[RAGChain] 权限过滤: 剔除 {_denied}/{len(docs)} 条越权证据"
                    f"(user_permissions={sorted(_user_perms) if _user_perms is not None else None})"
                )
                rag_permission_filtered_total.inc(_denied)
                get_chain_context().permission_filtered = _denied
                docs = _allowed
                input_dict["context"] = docs
        except Exception:  # noqa: BLE001
            logger.error("[RAGChain] 权限过滤异常，拒绝全部证据", exc_info=True)
            docs = []
            input_dict["context"] = []

        # ── 版本窗口过滤 ──
        try:
            from backend.rag.versioning import (
                candidate_versions,
                is_version_visible,
                normalize_requirement,
                version_conflict_info,
            )
            try:
                from backend.observability.metrics import rag_version_filtered_total
            except Exception:  # noqa: BLE001
                class _NopV:
                    def inc(self, _n=1):
                        pass
                rag_version_filtered_total = _NopV()
            _vreq = normalize_requirement(get_chain_context().version_requirement)
            if _vreq["type"] != "any":
                _v_allowed = [
                    d for d in docs if is_version_visible(d.metadata, _vreq)
                ]
                _v_denied = len(docs) - len(_v_allowed)
                if _v_denied:
                    logger.info(
                        f"[RAGChain] 版本过滤: 剔除 {_v_denied}/{len(docs)} 条窗口不匹配证据"
                        f"(requirement={_vreq['type']}"
                        f"{':' + _vreq['date'] if _vreq['date'] else ''})"
                    )
                    rag_version_filtered_total.inc(_v_denied)
                    docs = _v_allowed
                    input_dict["context"] = docs
                _v_event_data = {
                    "requirement": _vreq,
                    "candidates": candidate_versions([d.metadata for d in docs]),
                    "conflicts": version_conflict_info([d.metadata for d in docs]),
                    "filtered_out": _v_denied,
                }
                try:
                    from backend.observability.tracer import trace_collector as _tc
                    _v_trace = _tc.current()
                    _v_span = None
                    if _v_trace is not None and _v_trace.spans:
                        for _sp in reversed(_v_trace.spans):
                            if not _sp.end_time:
                                _v_span = _sp
                                break
                        _v_span = _v_span or _v_trace.spans[-1]
                    if _v_span is not None:
                        _tc.add_event(
                            _v_span, "version_filter", "info",
                            f"requirement={_vreq['type']} kept={len(docs)} "
                            f"candidates={len(_v_event_data['candidates'])}",
                            data=_v_event_data,
                        )
                except Exception:  # noqa: BLE001
                    logger.debug("[RAGChain] version_filter 事件写入失败", exc_info=True)
        except Exception:  # noqa: BLE001
            logger.debug("[RAGChain] 版本过滤异常，退化为不过滤", exc_info=True)

        # ── 证据 token 预算 ──
        evidence_budget = get_evidence_token_budget()
        if evidence_budget > 0 and docs:
            page_texts = [d.page_content for d in docs]
            rag_scores = [
                (d.metadata or {}).get("rerank_score") for d in docs]
            rag_sources = [
                str((d.metadata or {}).get("source_file") or "")
                for d in docs]
            if all(isinstance(s, (int, float)) for s in rag_scores):
                from backend.context_budget.rag_budgeter import (
                    budget_rag_indices,
                )
                kept_idx, dropped = budget_rag_indices(
                    page_texts, evidence_budget,
                    scores=[float(s) for s in rag_scores],
                    sources=rag_sources)
            else:
                from backend.context_budget.rag_budgeter import (
                    budget_rag_indices,
                )
                kept_idx, dropped = budget_rag_indices(
                    page_texts, evidence_budget)
            if dropped:
                docs = [docs[i] for i in kept_idx]
                input_dict["context"] = docs
                logger.info(
                    f"[RAGChain] 证据 token 预算裁剪: 丢弃 {dropped} 个低价值文档 "
                    f"(budget={evidence_budget}, "
                    f"score_aware={all(isinstance(s, (int, float)) for s in rag_scores)})")

        for i, doc in enumerate(docs, 1):
            doc.metadata["index"] = i
            sq = doc.metadata.get("source_query", "")
            doc.metadata["query_label"] = f"[Query: {sq}]\n" if sq else ""
            doc.metadata["doc_label"] = f"[文档: {doc.metadata.get('source_file', '')}]\n"
            section = doc.metadata.get("section_title", "")
            doc.metadata["section_label"] = f"[章节: {section}]\n" if section else ""
            cid = doc.metadata.get("chunk_id", "")
            doc.metadata["chunk_label"] = f"[Chunk: {cid}]\n" if cid else ""
            dt = doc.metadata.get("doc_type", "")
            doc.metadata["type_label"] = f"[类型: {dt}]\n" if dt and dt != "general" else ""
            bd = doc.metadata.get("business_domain", "")
            doc.metadata["domain_label"] = f"[业务域: {bd}]\n" if bd and bd != "general" else ""
        return input_dict

    _stuff = create_stuff_documents_chain(
        _llm_stream, _build_qa_prompt(),
        document_prompt=_build_document_prompt(),
        document_separator="\n\n---\n\n",
    )

    def _timed_stuff(inp):
        from backend.observability.tracer import SpanKind, trace_collector
        context_docs = inp.get("context", [])
        try:
            retrieval_metrics = _build_retrieval_span_metrics(context_docs)
            from backend.rag.retrieval_budget import current_budget

            _budget = current_budget()
            if _budget is not None:
                retrieval_metrics["budget_remaining_ms"] = round(
                    max(_budget.remaining_ms(), 0), 1)
            ret_span = trace_collector.end_open_span(
                "retrieval",
                metrics={"retrieved_chunks": len(context_docs),
                         **retrieval_metrics})
            if ret_span is not None:
                from backend.observability.metrics import record_rag_stage
                record_rag_stage("retrieve", ret_span.duration_ms)
        except Exception:
            logger.debug("[RAGChain] retrieval span 提前收口失败", exc_info=True)

        try:
            request_state = get_chain_context()
            retrieval_degraded = bool(
                request_state.rerank_unavailable
                or request_state.vector_degraded
            )
        except Exception:  # noqa: BLE001
            retrieval_degraded = False

        if retrieval_degraded:
            logger.info("[RAGChain] 检索关键环节降级，跳过生成以避免误导用户")
            try:
                from backend.observability.metrics import rag_short_circuit_total
                rag_short_circuit_total.labels(
                    reason="retrieval_degraded",
                ).inc()
            except Exception:  # noqa: BLE001
                pass
            return (
                '<!--META{"can_answer":false,"reason":"no_evidence",'
                '"confidence":0.0}-->'
            )

        if not context_docs:
            logger.info("[RAGChain] 空检索短路，跳过 LLM Generate")
            try:
                from backend.observability.metrics import rag_short_circuit_total
                rag_short_circuit_total.labels(reason="empty_retrieval").inc()
            except Exception:  # noqa: BLE001
                pass
            return AIMessage(content="知识库暂无相关资料。")

        gate_injected = (context_docs[0].metadata or {}).get("__evidence_gate_decision__") or {}
        if gate_injected.get("gate_passed") is False:
            logger.info(
                f"[RAGChain] Gate 前置拒答，跳过 LLM Generate: "
                f"reason={gate_injected.get('gate_reason')}")
            try:
                from backend.observability.metrics import rag_short_circuit_total
                rag_short_circuit_total.labels(reason="evidence_gate").inc()
            except Exception:  # noqa: BLE001
                pass
            return AIMessage(content="知识库暂无相关资料。")

        llm_span = trace_collector.start_span(
            "llm_generate", name="LLM生成",
            kind=SpanKind.LLM.value,
            input={"question": inp.get("input", "")[:1000]},
        )
        try:
            if get_enable_token_streaming():
                from backend.rag.meta_stream_filter import MetaStreamFilter
                mfilter = MetaStreamFilter()
                is_message = False
                for chunk in _stuff.stream(inp):
                    reasoning = extract_chunk_reasoning(chunk)
                    if reasoning:
                        emit_stream_delta(reasoning, kind="thinking")
                    text = extract_chunk_text(chunk)
                    if not text:
                        continue
                    mfilter.feed(text)
                    if not isinstance(chunk, str) and hasattr(chunk, "content"):
                        is_message = True
                    mfilter.flush()
                    joined = mfilter.full
                    r = AIMessage(content=joined) if is_message else joined
                else:
                    r = _stuff.invoke(inp)

            metrics = dict(get_chain_context().generation_call_meta)
            metrics["generation_input_chars"] = get_chain_context().generation_input_chars
            if not (metrics.get("total_tokens") or metrics.get("prompt_tokens")
                    or metrics.get("completion_tokens")):
                tu = trace_collector.parse_tokens(r)
                if tu:
                    metrics.update(tu)
                    metrics["token_source"] = "response_metadata"
                else:
                    metrics["token_source"] = "unavailable"
                    try:
                        from backend.observability.metrics import llm_usage_missing_total
                        llm_usage_missing_total.inc()
                    except Exception:
                        pass

            if isinstance(r, str):
                completion_text = r[:1000]
            elif hasattr(r, "content") and isinstance(r.content, str):
                completion_text = r.content[:1000]
            elif hasattr(r, "content"):
                completion_text = str(r.content)[:1000]
            if not completion_text:
                reasoning = (getattr(r, "additional_kwargs", {}) or {}).get("reasoning_content", "")
                if reasoning:
                    completion_text = f"[reasoning] {reasoning[:900]}"
            if completion_text:
                metrics["completion_text"] = completion_text

            logger.info(
                f"[RAGChain][diag] llm metrics keys={sorted(metrics.keys())} "
                f"ct_len={len(completion_text)} "
                f"r_type={type(r).__name__} "
                f"content_repr={repr(getattr(r, 'content', r))[:80]}")
            trace_collector.end_span(llm_span, metrics=metrics)
            from backend.observability.metrics import record_rag_stage
            record_rag_stage("generate", llm_span.duration_ms)
            return r
        except Exception:
            trace_collector.end_span(llm_span, status="error")
            from backend.observability.metrics import record_rag_stage
            record_rag_stage("generate", llm_span.duration_ms)
            raise

    stuff_chain = RunnableLambda(_index_docs) | RunnableLambda(_timed_stuff)

    # ⑤ Hybrid Retrieval
    retriever = chain_instance.chunk_retriever_base

    # ④ Adaptive
    retriever = AdaptiveRetriever(
        base_retriever=retriever,
        doc_db=chain_instance.doc_db,
    )

    # ③ MultiQuery
    from backend.rag.retrieval.multi_query import MultiQueryRetriever
    retriever = MultiQueryRetriever(base_retriever=retriever)
    chain_instance._mq_retriever = retriever

    # ② Rerank
    from langchain_classic.retrievers import ContextualCompressionRetriever
    retriever = ContextualCompressionRetriever(
        base_compressor=RerankCompressor(),
        base_retriever=retriever,
    )
    chain_instance._rerank_wrapper = retriever

    # Gate 前置
    gate_retriever = RunnableLambda(chain_instance._gate_wrap_retrieve)

    # ① HistoryAware
    chain_instance.chain_standalone = create_retrieval_chain(gate_retriever, stuff_chain)
    if get_enable_history_aware():
        chain_instance._runtime_prompt_keys.append("rag.contextualize")
        retriever = create_history_aware_retriever(
            get_chain_llm(), gate_retriever, _build_contextualize_prompt()
        )

    chain_instance.chain = create_retrieval_chain(retriever, stuff_chain)


def register_prompt_reload_hooks(chain_instance) -> None:
    """注册 Prompt 热加载回调。"""
    try:
        from backend.prompts.service import prompt_service
        for key in chain_instance._runtime_prompt_keys:
            prompt_service.register_reload_hook(key, chain_instance._mark_chains_dirty)
    except Exception:
        logger.debug("[RAGChain] prompt_service 不可用，跳过热加载注册", exc_info=True)
