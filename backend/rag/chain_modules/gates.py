"""RAG Chain 证据门禁 (Evidence Gates) 判定与检索包装。"""
from __future__ import annotations

from backend.rag.chain_modules.metrics_helper import _mark_gate_degraded
from backend.shared.logger import logger


def gate_wrap_retrieve(chain_instance, payload):
    """Gate 前置检索包装：调底层检索 → 执行 Evidence Gate → 注入 decision。

    create_retrieval_chain / history_aware_retriever 的 retriever 槽位。
    payload 兼容 dict（{"input": ...}）与 str 两种输入形态。
    实体覆盖校验使用 chain_instance._last_query（原始用户问题）——history-aware
    改写后的 query 不反映用户原始实体。
    """
    query = payload.get("input") if isinstance(payload, dict) else payload
    docs = list(chain_instance._rerank_wrapper.invoke(query))
    try:
        from backend.rag.evidence_gate import is_evidence_gate_enabled
        if is_evidence_gate_enabled() and docs:
            question = chain_instance._last_query or (query if isinstance(query, str) else str(query))
            decision = chain_instance._run_evidence_gates(question, docs)
            if decision is not None:
                stamp = {
                    "gate_passed": bool(decision.passed),
                    "gate_layer": decision.layer or "retrieval",
                    "gate_score": float(decision.score or 0.0),
                    "gate_reason": (decision.reason.value if decision.reason else ""),
                    **(decision.diagnostics or {}),
                }
                for d in docs:
                    d.metadata["__evidence_gate_decision__"] = stamp
    except Exception as e:  # noqa: BLE001
        # Gate 前置失败 → 不拦截 docs，交由原兜底路径处理（软降级，留痕）
        logger.warning(f"[RAGChain] Gate 前置评估失败，透传 docs: {e}", exc_info=True)
        _mark_gate_degraded("pre_wrap", "passthrough", e)
    return docs


def decision_from_injected(injected):
    """doc.metadata 注入的 decision dict → GateDecision 对象。

    无注入（空 docs / Gate 关闭）→ 透传放行，与原 _run_evidence_gates
    的兜底行为一致。
    """
    from backend.rag.evidence_gate import (
        gate_retrieval_passthrough,
        is_evidence_gate_enabled,
    )
    if injected is None or not is_evidence_gate_enabled():
        return gate_retrieval_passthrough()
    from backend.rag.evidence_gate import GateDecision, RejectReason
    try:
        return GateDecision(
            passed=bool(injected.get("gate_passed")),
            reason=(RejectReason(injected["gate_reason"])
                    if injected.get("gate_reason") else None),
            layer="retrieval",
            score=float(injected.get("gate_score", 0.0)),
            diagnostics={k: v for k, v in injected.items()
                         if k not in ("gate_passed", "gate_layer",
                                      "gate_score", "gate_reason")},
        )
    except Exception as e:
        logger.warning(f"[RAGChain] Gate decision 反序列化失败，透传放行: {e}")
        return gate_retrieval_passthrough()


def run_evidence_gates(chain_instance, question: str, context_docs: list):
    """两层 Gate（Retrieval + Rerank）的合并判定。

    行为契约：
      - 任一 Gate passed=False → 返回该 GateDecision
      - 都通过 → 返回最后一个 passed=True 的 GateDecision
      - 总开关关闭 / 任何异常 → 返回 passed=True (透传)
    """
    from backend.observability.tracer import SpanKind, trace_collector
    from backend.rag.evidence_gate import (
        GateDecision,
        RejectReason,
        assess_query_scope,
        evidence_gate_rerank,
        evidence_gate_retrieval,
        gate_retrieval_passthrough,
        is_evidence_gate_enabled,
    )

    if not is_evidence_gate_enabled():
        return gate_retrieval_passthrough()

    # ── Gate 1: Retrieval（hybrid.py 注入的 decision 作为快路径）──
    gate_span = trace_collector.start_span(
        "evidence_gate_retrieval", name="Evidence Gate - Retrieval",
        kind=SpanKind.RETRIEVAL_GATE.value,
    )

    # Gate 0: 当前 RAG 不具备实时外部数据、凭据披露或越权操作能力时，
    # 先按查询范围拒答，避免相似文档把不应回答的请求误判为有证据。
    scope = assess_query_scope(question)
    if scope.blocked:
        scope_decision = GateDecision(
            passed=False,
            reason=RejectReason.NO_EVIDENCE,
            layer="retrieval",
            score=0.0,
            diagnostics={
                "query_scope": "blocked",
                "query_scope_category": scope.category,
                "query_scope_reason": scope.reason,
            },
        )
        trace_collector.end_span(
            gate_span,
            metrics=scope_decision.to_metrics(),
            status="rejected",
        )
        return scope_decision

    # 优先复用 hybrid.py 注入的 decision
    injected = (context_docs[0].metadata.get("__evidence_gate_decision__")
                if context_docs else None)
    gate1_degraded = False
    if injected is not None:
        try:
            ret_decision = GateDecision(
                passed=bool(injected.get("gate_passed")),
                reason=(RejectReason(injected["gate_reason"])
                        if injected.get("gate_reason") else None),
                layer="retrieval",
                score=float(injected.get("gate_score", 0.0)),
                diagnostics={k: v for k, v in injected.items()
                             if k not in ("gate_passed", "gate_layer",
                                          "gate_score", "gate_reason")},
            )
        except Exception as e:
            logger.warning(f"[RAGChain] Gate 1 decision 反序列化失败，透传放行: {e}")
            ret_decision = gate_retrieval_passthrough()
            gate1_degraded = True
            _mark_gate_degraded("gate1_deserialize", "passthrough", e, span=gate_span)
    else:
        # 没注入（空召回或 fallback 路径）→ 自己跑一次
        try:
            from backend.config import DOC_TYPE_COVERAGE_REQUIRED, VEC_MIN_SCORE
            ret_decision = evidence_gate_retrieval(
                context_docs,
                query_analysis=chain_instance.gate.query_analysis,
                vec_min_score=VEC_MIN_SCORE,
                require_doc_type_coverage=DOC_TYPE_COVERAGE_REQUIRED,
            )
        except Exception as e:
            logger.warning(f"[RAGChain] Gate 1 评估异常，透传放行: {e}", exc_info=True)
            ret_decision = gate_retrieval_passthrough()
            gate1_degraded = True
            _mark_gate_degraded("gate1", "passthrough", e, span=gate_span)

    trace_collector.end_span(
        gate_span, metrics=ret_decision.to_metrics(),
        status=("skipped" if gate1_degraded
                else ("success" if ret_decision.passed else "rejected")))

    if not ret_decision.passed:
        return ret_decision

    # ── Gate 1.5: 查询实体覆盖校验（P2，2026-08-21）──
    if context_docs:
        try:
            from backend.config import GATE_ENTITY_CHECK_ENABLED
            if GATE_ENTITY_CHECK_ENABLED:
                _injected_diags = (ret_decision.diagnostics or {})
                _hybrid_checked = (
                    injected is not None
                    and _injected_diags.get("entity_check") in ("pass", "fail")
                )
                if _hybrid_checked:
                    logger.debug(
                        "[RAGChain] 实体覆盖校验沿用 hybrid 注入判定: "
                        "entity_check=%s", _injected_diags.get("entity_check"))
                else:
                    from backend.rag.evidence_gate import (
                        find_missing_entities,
                    )
                    missing = find_missing_entities(question, context_docs)
                    if missing:
                        ret_decision = GateDecision(
                            passed=False, reason=RejectReason.NO_EVIDENCE,
                            layer="retrieval", score=ret_decision.score,
                            diagnostics={**(ret_decision.diagnostics or {}),
                                         "entity_check": "fail",
                                         "missing_entities": missing[:5]},
                        )
                        logger.info(
                            f"[RAGChain] 实体覆盖校验拒答: missing={missing[:5]}"
                        )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[RAGChain] 实体覆盖校验异常，忽略: {e}")
            _mark_gate_degraded("gate1_entity", "skip_check", e)

    if not ret_decision.passed:
        return ret_decision

    # ── Gate 2: Rerank（基于 context 上的 rerank_score）──
    from backend.rag.evidence_gate import risk_level_from_intent_and_doctype
    try:
        chain_instance.gate.set_risk_level(risk_level_from_intent_and_doctype(
            chain_instance.gate.intent,
            getattr(chain_instance.gate.query_analysis, "doc_types", []) or [],
        ))
    except Exception as e:
        logger.debug(f"[RAGChain] 风险等级推导失败，按 low 处理: {e}", exc_info=True)
        chain_instance.gate.set_risk_level("low")
        _mark_gate_degraded("risk_level", "low_fallback", e)

    rerank_span = trace_collector.start_span(
        "evidence_gate_rerank", name="Evidence Gate - Rerank",
        kind=SpanKind.RERANK_GATE.value,
    )
    rerank_degraded = False
    try:
        from backend.config import (
            RERANK_HIGH_RISK_MIN_TOP1,
            RERANK_MIN_AVG,
            RERANK_MIN_GAP,
            RERANK_MIN_TOP1,
        )
        rerank_decision = evidence_gate_rerank(
            context_docs,
            intent=chain_instance.gate.intent,
            risk_level=chain_instance.gate.risk_level,
            min_top1=RERANK_MIN_TOP1,
            min_avg=RERANK_MIN_AVG,
            min_gap=RERANK_MIN_GAP,
            high_risk_min_top1=RERANK_HIGH_RISK_MIN_TOP1,
        )
    except Exception as e:
        logger.warning(f"[RAGChain] Gate 2 评估异常，透传放行: {e}", exc_info=True)
        rerank_decision = gate_retrieval_passthrough()
        rerank_degraded = True
        _mark_gate_degraded("gate2", "passthrough", e, span=rerank_span)

    trace_collector.end_span(
        rerank_span,
        metrics=rerank_decision.to_metrics(),
        status=("skipped" if rerank_degraded
                else ("success" if rerank_decision.passed else "rejected")))

    return rerank_decision


def record_retrieval_events(ret_span, context_docs: list) -> None:
    """采集检索各阶段的中间结果，写入 ret_span.events。"""
    from backend.observability.tracer import trace_collector

    # ── Event 1: Query Analyzer ──
    try:
        from backend.rag.chain_modules.compat import get_chain_context
        pq = get_chain_context().query_analysis
        if pq is not None:
            trace_collector.add_event(ret_span, "query_analyzer", "info",
                f"intent={pq.intent}, doc_types={pq.doc_types}",
                data={"intent": pq.intent, "doc_types": pq.doc_types,
                      "metadata_filter": pq.to_metadata_filter()})
    except Exception:
        logger.debug("query_analysis span 记录失败", exc_info=True)

    # ── Event 2: Rerank 结果 ──
    rerank_scores = []
    for doc in context_docs[:10]:
        score = doc.metadata.get("rerank_score")
        if score is not None:
            rerank_scores.append({
                "chunk_id": doc.metadata.get("chunk_id", ""),
                "score": round(score, 4),
                "snippet": doc.page_content[:120],
                "source": doc.metadata.get("source_file", ""),
                "source_query": doc.metadata.get("source_query", ""),
                "doc_type": doc.metadata.get("doc_type", ""),
            })
    if rerank_scores:
        trace_collector.add_event(ret_span, "rerank", "info",
            f"top {len(rerank_scores)} scored chunks",
            data={"scored": rerank_scores})

    # ── Event 3: Final Context ──
    trace_collector.add_event(ret_span, "final_context", "info",
        f"{len(context_docs)} chunks → LLM",
        data={"chunks": [{
            "chunk_id": d.metadata.get("chunk_id", ""),
            "source": d.metadata.get("source_file", ""),
            "source_query": d.metadata.get("source_query", ""),
            "doc_type": d.metadata.get("doc_type", ""),
            "keywords": d.metadata.get("chunk_keywords", ""),
            "snippet": d.page_content[:100],
        } for d in context_docs[:8]]})
