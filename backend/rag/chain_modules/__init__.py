"""RAG Chain 模块化子包。"""
from __future__ import annotations

from .builder import build_rag_chains, build_retrievers, register_prompt_reload_hooks
from .compat import (
    get_chain_context,
    get_chain_llm,
    get_enable_history_aware,
    get_enable_token_streaming,
    get_evidence_token_budget,
)
from .gates import decision_from_injected, gate_wrap_retrieve, record_retrieval_events, run_evidence_gates
from .lifecycle import (
    emit_embedding_stats,
    end_root_span,
    execute_chain,
    finish_error,
    finish_response,
    handle_llm_reject,
    prepare_session,
    record_rag_metric,
    record_runtime_prompt_usage,
    reject,
    remember_turn,
    respond,
    start_trace,
    try_self_correct,
)
from .metrics_helper import _build_retrieval_span_metrics, _mark_gate_degraded
from .prompts import (
    _HISTORY_REWRITE_CUES,
    DEFAULT_CONTEXTUALIZE_SYSTEM,
    DEFAULT_DOCUMENT_TEMPLATE,
    DEFAULT_QA_SYSTEM,
    _build_contextualize_prompt,
    _build_document_prompt,
    _build_qa_prompt,
    _needs_history_rewrite,
)
from .streaming import _llm_stream
from .verification import evaluate_faithfulness, verify_answer, verify_claims

__all__ = [
    "DEFAULT_CONTEXTUALIZE_SYSTEM",
    "DEFAULT_DOCUMENT_TEMPLATE",
    "DEFAULT_QA_SYSTEM",
    "_HISTORY_REWRITE_CUES",
    "_build_contextualize_prompt",
    "_build_document_prompt",
    "_build_qa_prompt",
    "_build_retrieval_span_metrics",
    "_llm_stream",
    "_mark_gate_degraded",
    "_needs_history_rewrite",
    "build_rag_chains",
    "build_retrievers",
    "decision_from_injected",
    "emit_embedding_stats",
    "end_root_span",
    "evaluate_faithfulness",
    "execute_chain",
    "finish_error",
    "finish_response",
    "gate_wrap_retrieve",
    "get_chain_context",
    "get_chain_llm",
    "get_enable_history_aware",
    "get_enable_token_streaming",
    "get_evidence_token_budget",
    "handle_llm_reject",
    "prepare_session",
    "record_rag_metric",
    "record_retrieval_events",
    "record_runtime_prompt_usage",
    "register_prompt_reload_hooks",
    "reject",
    "remember_turn",
    "respond",
    "run_evidence_gates",
    "start_trace",
    "try_self_correct",
    "verify_answer",
    "verify_claims",
]
