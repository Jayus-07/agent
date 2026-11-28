"""RAG Chain 问答生命周期、执行与响应裁决。"""
from __future__ import annotations

import time as _time

from backend.config.rag import RAG_RETRIEVAL_DEADLINE_MS
from backend.rag.chain_modules.compat import get_chain_context
from backend.rag.chain_modules.prompts import _needs_history_rewrite
from backend.rag.retrieval_budget import clear_retrieval_budget, start_retrieval_budget
from backend.shared.logger import logger


def start_trace(chain_instance, question: str, session_id: str):
    """开启 Trace + root span。"""
    from backend.observability.tracer import _scope_root_var, get_external_child_trace_id, trace_collector
    external_child_id = get_external_child_trace_id()
    prev = trace_collector.current()
    if prev is not None and not external_child_id:
        trace = prev
        trace._rag_embedded = True
        trace_collector.start_span(
            "rag_skill", name="RAG 智能问答", type="agent",
            kind="skill", input={"question": question})
        _scope_root_var.set("rag_skill")
        logger.info(f"[RAGChain] 嵌入模式（父 trace={prev.id[:8]}）: {question[:60]}...")
    else:
        from backend.observability.tracer import (
            WorkflowKind,
            get_external_parent_trace_id,
        )

        trace = trace_collector.start(
            question,
            session_id,
            parent_trace_id=get_external_parent_trace_id(),
            trace_id=external_child_id,
            workflow_name="rag_agent",
            workflow_kind=WorkflowKind.RAG_QUERY.value,
        )
        trace._rag_embedded = False
        trace_collector.start_span("root", parent_id=None,
                                   name="RAG 智能问答", type="agent",
                                   input={"question": question})
        logger.info(f"[RAGChain] 收到问题: {question[:60]}... (session={session_id})")
    chain_instance._record_runtime_prompt_usage()
    return trace, _time.time()


def record_runtime_prompt_usage(chain_instance) -> None:
    """把本次 RAG 链实际依赖的 Prompt 版本写入当前 Trace。"""
    try:
        from backend.prompts.service import prompt_service

        for key in chain_instance._runtime_prompt_keys:
            prompt_service.record_template_usage_sync(key)
    except Exception:  # noqa: BLE001
        logger.debug("[RAGChain] Prompt 运行版本记录失败", exc_info=True)


def remember_turn(chain_instance, session_id: str, question: str, answer: str,
                  trace=None, persist_memory: bool = True) -> None:
    """memory 唯一写点。"""
    if not persist_memory or (
        trace is not None and getattr(trace, "_rag_embedded", False)
    ):
        return
    if chain_instance._memory:
        try:
            from backend.core.request_context import get_current_product_id
            identity = get_chain_context().identity
            user_id = (getattr(identity, "user_id", "") or "default")
            chain_instance._memory.end_turn(session_id, question, answer,
                                            user_id=user_id,
                                            tenant_id=getattr(identity, "tenant_id", "") or "",
                                            product=get_current_product_id(),
                                            trace_id=str(getattr(trace, "id", "") or "") if trace is not None else "")
        except Exception:
            logger.debug("[RAGChain] memory end_turn 失败（不影响应答）",
                         exc_info=True)


def emit_embedding_stats(stats) -> None:
    """把单请求 embedding 统计落到请求上下文 / Trace / 日志 / Prometheus。"""
    try:
        get_chain_context().embedding_stats = stats
    except Exception:  # noqa: BLE001
        pass
    if stats is None:
        return
    try:
        from backend.observability.metrics import record_rag_stage
        record_rag_stage("embedding", stats.duration_ms)
    except Exception:  # noqa: BLE001
        pass
    try:
        from backend.observability.tracer import trace_collector
        trace = trace_collector.current()
        span = trace.spans[-1] if trace is not None and trace.spans else None
        if span is not None:
            trace_collector.add_event(
                span, "embedding_stats", "info",
                "检索期 embedding 出站/复用统计",
                data=stats.as_dict(),
            )
    except Exception:  # noqa: BLE001
        pass
    logger.info(
        f"[RAGChain] embedding 统计: requested={stats.requested} "
        f"computed={stats.computed} request_hits={stats.request_hits} "
        f"process_hits={stats.process_hits} unique={stats.unique_inputs} "
        f"out_ms={stats.duration_ms:.1f} dedup={stats.dedup_ratio:.2f}"
    )


def record_rag_metric(status: str) -> None:
    """埋点 RAG 查询结果到运营指标。"""
    try:
        from backend.observability.metrics import record_rag_status
        record_rag_status(status)
    except Exception as e:
        logger.debug(f"[RAGChain] 指标埋点失败: {e}", exc_info=True)


def handle_llm_reject(chain_instance, meta, trace, question, session_id, t_total,
                      persist_memory: bool = True) -> str:
    """Gate 3（LLM 自报拒答）的统一处理。"""
    decision = chain_instance.gate.build_decision_from_meta(meta)

    if chain_instance.corrector.can_retry():
        retried = chain_instance._try_self_correct(decision, trace, question, session_id, t_total)
        if retried is not None:
            chain_instance._remember_turn(
                session_id, question, retried, trace=trace,
                persist_memory=persist_memory,
            )
            chain_instance._finish(trace, retried, t_total)
            return retried

    attempted = chain_instance.corrector.retry_count > 0
    return chain_instance._reject(decision, "generation", trace, t_total,
                                  self_correction_attempted=attempted)


def reject(chain_instance, decision, layer: str, trace, t_total: float,
           self_correction_attempted: bool = False) -> str:
    """统一拒答路径。"""
    from backend.rag.evidence_gate import build_rejection_response
    from backend.rag.evidence_gate.models import (
        ANSWER_STATUS_PERMISSION_DENIED,
        ANSWER_STATUS_RETRIEVAL_DEGRADED,
        PERMISSION_FILTERED_MESSAGE,
        resolve_answer_status,
    )
    try:
        request_state = get_chain_context()
        permission_filtered = int(request_state.permission_filtered or 0)
        retrieval_degraded = bool(
            request_state.rerank_unavailable or request_state.vector_degraded
        )
    except Exception:  # noqa: BLE001
        permission_filtered = 0
        retrieval_degraded = False

    answer_status = resolve_answer_status(
        getattr(decision, "reason", None), permission_filtered,
        retrieval_degraded=retrieval_degraded,
    )
    msg, info = build_rejection_response(decision, layer,
                                         self_correction_attempted=self_correction_attempted,
                                         permission_filtered=permission_filtered)
    if answer_status == ANSWER_STATUS_PERMISSION_DENIED:
        msg = PERMISSION_FILTERED_MESSAGE
    elif answer_status == ANSWER_STATUS_RETRIEVAL_DEGRADED:
        msg = "这次查询没有完成，我暂时无法判断知识库里是否有相关资料。"

    try:
        get_chain_context().meta["answer_status"] = answer_status
    except Exception:  # noqa: BLE001
        logger.debug("answer_status 写入请求上下文失败", exc_info=True)

    try:
        from backend.security.events import record_security_event
        record_security_event(
            "EVIDENCE_REJECT",
            category=str(getattr(decision, "reason", "") or "unknown"),
            detail={"layer": layer,
                    "self_correction_attempted": self_correction_attempted},
        )
    except Exception:
        pass

    try:
        trace.metadata["rejection"] = info.to_dict()
    except Exception:
        logger.debug("trace metadata rejection 写入失败", exc_info=True)

    metrics = {"rejected": True}
    if self_correction_attempted:
        metrics["self_correction"] = "attempted"
    chain_instance._end_root_span(trace,
        output={"answer_preview": msg[:200], "answer_len": len(msg)},
        metrics=metrics)
    chain_instance._finish(trace, msg, t_total)
    logger.info(f"[RAGChain] 拒答 layer={layer} reason={info.reason}")
    return msg


def finish_response(chain_instance, trace, answer: str, t_total: float) -> None:
    """统一 trace 收尾。"""
    from backend.config.llm import LLM_MODEL
    from backend.observability.tracer import _scope_root_var, trace_collector
    total_ms = int((_time.time() - t_total) * 1000)
    chain_instance._end_root_span(trace,
        output={"answer_preview": answer[:200], "answer_len": len(answer)},
        metrics={"span_count": sum(1 for s in trace.spans if s.parent_id is not None)})
    if getattr(trace, '_rag_embedded', False):
        _scope_root_var.set(None)
        return
    provider = ""
    try:
        from backend.infra.llm.resolved_model import get_current_resolved_model
        _ctx = get_current_resolved_model()
        if _ctx is not None and _ctx.model_id:
            trace_collector.finish(trace, answer, total_ms,
                                   _ctx.model_id, _ctx.provider)
            return
        from backend.config import model_roles as _model_roles
        from backend.infra.llm.models import resolve_provider as _resolve_provider

        actual_model = str(_model_roles.resolve_effective("main").get("value")
                           or LLM_MODEL)
        provider = _resolve_provider(actual_model)
        trace_collector.finish(trace, answer, total_ms, actual_model, provider)
    except Exception:
        logger.debug("LLM provider 检测失败", exc_info=True)
        trace_collector.finish(trace, answer, total_ms, LLM_MODEL, provider)


def finish_error(chain_instance, trace, t_total: float) -> None:
    """异常路径收尾。"""
    from backend.observability.tracer import _scope_root_var, trace_collector
    try:
        chain_instance._end_root_span(trace, status="error",
                                      metrics={"error": "pipeline_failed"})
        if getattr(trace, '_rag_embedded', False):
            _scope_root_var.set(None)
            return
        trace_collector.finish(trace, "[ERROR]",
                               int((_time.time() - t_total) * 1000), "", "")
    except Exception as e:
        logger.error("[RAGChain] error cleanup failed: %s", e, exc_info=True)


def end_root_span(trace, output: dict = None, metrics: dict = None,
                  status: str = "success") -> None:
    """查找并结束 root span。"""
    from backend.observability.tracer import trace_collector
    if getattr(trace, '_rag_embedded', False):
        for sp in trace.spans:
            if sp.span_id == "rag_skill":
                trace_collector.end_span(sp, output=output, metrics=metrics, status=status)
                return
        return
    for sp in trace.spans:
        if sp.parent_id is None:
            trace_collector.end_span(sp, output=output, metrics=metrics, status=status)
            return


def try_self_correct(chain_instance, original_decision, trace, question, session_id, t_total):
    """Self-Correction：改写 query 重试。"""
    chain_instance.corrector.record_attempt(success=False)
    reason_str = (original_decision.reason.value
                  if original_decision.reason else "no_evidence")
    new_query = chain_instance.corrector.try_rewrite(question, reason_str)
    if new_query is None:
        return None

    try:
        history = chain_instance._prepare(new_query, session_id)
        result = chain_instance._execute(new_query, history)

        decision = result.get("__evidence_gate_decision__")
        if decision is not None and not decision.passed:
            return None

        answer = chain_instance._verify(result, new_query, session_id)
        answer = chain_instance._verify_claims(answer, result.get("context", []))
        if answer is None:
            return None
        answer = chain_instance._evaluate(answer, result.get("context", []))
        meta = chain_instance._last_meta or {}
        if not meta.get("can_answer", True):
            return None
        logger.info(f"[RAGChain] Self-Correction 救活: question={question[:60]}")
        return answer
    except Exception as e:
        logger.warning(f"[Self-Correction] 重试失败: {e}")
        return None


def prepare_session(chain_instance, question: str, session_id: str) -> list:
    """准备阶段：Memory 启动会话，返回 chat_history。"""
    identity = get_chain_context().identity
    user_id = getattr(identity, "user_id", "") or "default"
    tenant_id = getattr(identity, "tenant_id", "") or ""
    from backend.core.request_context import get_current_product_id
    l1 = (chain_instance._memory.start_session(session_id, question, user_id=user_id,
                                               tenant_id=tenant_id,
                                               product=get_current_product_id(),
                                               timeout=1.5)
          if chain_instance._memory else None)
    return list(l1.messages) if l1 else []


def execute_chain(chain_instance, question: str, chat_history: list) -> dict:
    """执行阶段：chain.invoke + MultiQuery 决策 trace + Retrieval Debug。"""
    from backend.observability.tracer import SpanName as _SpanName
    from backend.observability.tracer import trace_collector

    chain_instance._last_query = question
    try:
        from backend.rag.retrieval.query_analyzer import QueryAnalyzer
        qa_result = QueryAnalyzer().analyze(question)
        get_chain_context().query_analysis = qa_result
        chain_instance.gate.set_query_analysis(qa_result)
    except Exception as e:
        logger.debug(f"[RAGChain] QueryAnalyzer 分析失败: {e}", exc_info=True)
        chain_instance.gate.set_query_analysis(None)

    ret_span = trace_collector.start_span(
        "retrieval",
        name=_SpanName.RETRIEVAL, type="retrieval",
        kind="retrieval",
        input={"question": question[:500]},
    )

    history_rewrite_used = (
        bool(chat_history)
        and _needs_history_rewrite(question)
        and getattr(chain_instance, "chain", None) is not None
    )
    active_chain = chain_instance.chain if history_rewrite_used else chain_instance.chain_standalone

    start_retrieval_budget(RAG_RETRIEVAL_DEADLINE_MS)
    try:
        result = active_chain.invoke({"input": question, "chat_history": chat_history})
        if history_rewrite_used and not (
            isinstance(result, dict) and result.get("context")
        ):
            logger.info("[RAGChain] 历史改写检索无证据，回退原问题重试")
            result = chain_instance.chain_standalone.invoke(
                {"input": question, "chat_history": []}
            )
    finally:
        clear_retrieval_budget()

    context_docs = result.get("context", [])
    chain_instance._record_retrieval_events(ret_span, context_docs)
    trace_collector.end_span(ret_span, metrics={"total_docs": len(context_docs)})

    mq_span = trace_collector.start_span("mq_check", name=_SpanName.MULTI_QUERY)
    mq = getattr(chain_instance, '_mq_retriever', None)
    triggered = mq._last_triggered if mq else False
    from backend.rag.retrieval.multi_query import get_mq_mode
    trace_collector.end_span(mq_span,
        metrics={"triggered": triggered, "mode": get_mq_mode()},
        status="skipped" if not triggered else "success")

    injected = (context_docs[0].metadata.get("__evidence_gate_decision__")
                if context_docs else None)
    if injected is not None:
        result["__evidence_gate_decision__"] = chain_instance._decision_from_injected(injected)
    else:
        result["__evidence_gate_decision__"] = chain_instance._run_evidence_gates(
            question, context_docs
        )

    return result


def respond(chain_instance, result, trace, question, session_id, t_total,
            persist_memory: bool = True) -> str:
    """统一决策：Gate 1+2(注入) → verify+evaluate → Gate 3 LLM 自报 → Self-Correction。"""
    decision = result.get("__evidence_gate_decision__")
    if decision is not None and not decision.passed:
        answer = chain_instance._reject(decision, decision.layer or "retrieval",
                                         trace, t_total)
        chain_instance._record_rag_metric("rejected")
        return answer

    answer = chain_instance._verify(result, question, session_id)

    answer = chain_instance._verify_claims(answer, result.get("context", []))
    if answer is None:
        from backend.rag.evidence_gate import GateDecision, RejectReason
        decision = GateDecision(
            passed=False, reason=RejectReason.HALLUCINATION,
            layer="claim_verify", score=0.0,
            diagnostics={"reason": "numeric_fact_not_supported"},
        )
        answer = chain_instance._reject(decision, "claim_verify", trace, t_total)
        chain_instance._record_rag_metric("rejected")
        return answer

    answer = chain_instance._evaluate(answer, result.get("context", []))

    if chain_instance._last_faithfulness is not None:
        from backend.config import (
            FAITHFULNESS_REJECT_SCORE,
            HIGH_RISK_REJECT_SCORE,
        )
        from backend.rag.evidence_gate import (
            GateDecision,
            RejectReason,
            is_groundedness_acceptable,
        )
        acceptable, _ = is_groundedness_acceptable(
            chain_instance._last_faithfulness.score,
            risk_level=chain_instance.gate.risk_level,
            low_threshold=FAITHFULNESS_REJECT_SCORE,
            high_threshold=HIGH_RISK_REJECT_SCORE,
        )
        if not acceptable:
            decision = GateDecision(
                passed=False, reason=RejectReason.HALLUCINATION,
                layer="evaluation", score=chain_instance._last_faithfulness.score,
                diagnostics={"reason": "faithfulness_below_threshold"},
            )
            answer = chain_instance._reject(decision, "evaluation", trace, t_total)
            chain_instance._record_rag_metric("rejected")
            return answer

    meta = chain_instance._last_meta or {}
    if not meta.get("can_answer", True):
        answer = chain_instance._handle_llm_reject(
            meta, trace, question, session_id, t_total,
            persist_memory=persist_memory,
        )
        chain_instance._record_rag_metric("rejected")
        return answer

    if chain_instance._last_faithfulness and chain_instance._last_faithfulness.score >= 0.99:
        chain_instance._record_rag_metric("fallback")
    else:
        chain_instance._record_rag_metric("hit")

    partial_retrieval = any(
        (doc.metadata or {}).get("_mq_partial")
        or (doc.metadata or {}).get("_retrieval_partial")
        for doc in result.get("context", [])
    )
    if partial_retrieval:
        from backend.rag.evidence_gate.models import (
            ANSWER_STATUS_RETRIEVAL_DEGRADED,
        )

        try:
            get_chain_context().meta["answer_status"] = (
                ANSWER_STATUS_RETRIEVAL_DEGRADED
            )
        except Exception:  # noqa: BLE001
            logger.debug("RAG 部分检索状态写入请求上下文失败", exc_info=True)
        try:
            trace.metadata["answer_status"] = (
                ANSWER_STATUS_RETRIEVAL_DEGRADED
            )
        except Exception:  # noqa: BLE001
            logger.debug("RAG 部分检索状态写入 Trace 失败", exc_info=True)

        answer = (
            "提示：部分知识库检索路径未能完成，以下内容仅依据已检索到的资料，"
            "可能不完整。\n\n"
            + answer
        )

    chain_instance._remember_turn(
        session_id, question, answer, trace=trace,
        persist_memory=persist_memory,
    )
    chain_instance._finish(trace, answer, t_total)
    return answer
