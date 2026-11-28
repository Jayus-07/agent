"""LangChain LCEL 主 Chain 门面。

流水线（外层先执行）:
  ① HistoryAware   — Query Understanding: 对话历史改写指代/省略
  ② MultiQuery     — Query Expansion:   关键词检测复杂度 → LLM 改写
  ③ ChunkLevel     — Hybrid Retrieval:  向量 + BM25 混合检索
  ④ Adaptive       — Document Expansion: 同文档相邻 Chunk 扩展
  ⑤ Rerank         — CrossEncoder:      全局重排序 + 阈值过滤
  ⑥ LLM Generate   — 带引用标注 [1][2]

三层记忆:
  L1 短期: 当前调用的消息缓冲区
  L2 会话: PostgreSQL 持久化 (via SessionRepository)
  L3 长期: PostgreSQL + pgvector (via MemoryRepository)
  MemoryManager 统一管理三层，chain 只持有引用。

架构解耦说明：
  底层实现已物理拆解至 backend.rag.chain_modules 子包：
  - prompts: Prompt 模板常量与构建器
  - streaming: _llm_stream 生成器
  - metrics_helper: 检索阶段计数与防线软降级
  - builder: 检索器与 LCEL 执行链构建器
  - gates: Evidence Gates 门禁判定与检索包装
  - verification: 事实校验、Claim 比对与忠实度检测
  - lifecycle: 链路 Trace、会话准备、执行调度与响应收尾
  本模块作为 100% 向后兼容门面，对外统一重新导出所有符号与 RAGChain 接口。
"""
from __future__ import annotations

import time as _time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from backend.config import (
    ENABLE_HISTORY_AWARE_RETRIEVAL,
    ENABLE_TOKEN_STREAMING,
    EVIDENCE_TOKEN_BUDGET,
)
from backend.infra.llm import llm
from backend.infra.llm.proxy import (
    emit_stream_delta,
    extract_chunk_reasoning,
    extract_chunk_text,
)
from backend.rag.chain_modules import (
    _HISTORY_REWRITE_CUES,
    DEFAULT_CONTEXTUALIZE_SYSTEM,
    DEFAULT_DOCUMENT_TEMPLATE,
    DEFAULT_QA_SYSTEM,
    _build_contextualize_prompt,
    _build_document_prompt,
    _build_qa_prompt,
    _build_retrieval_span_metrics,
    _llm_stream,
    _mark_gate_degraded,
    _needs_history_rewrite,
    build_rag_chains,
    build_retrievers,
    decision_from_injected,
    emit_embedding_stats,
    end_root_span,
    evaluate_faithfulness,
    execute_chain,
    finish_error,
    finish_response,
    gate_wrap_retrieve,
    handle_llm_reject,
    prepare_session,
    record_rag_metric,
    record_retrieval_events,
    record_runtime_prompt_usage,
    register_prompt_reload_hooks,
    reject,
    remember_turn,
    respond,
    run_evidence_gates,
    start_trace,
    try_self_correct,
    verify_answer,
    verify_claims,
)
from backend.rag.citation import CitationFormatter
from backend.rag.context import clear_context, get_context, set_context
from backend.rag.evidence_gate import EvidenceGateController
from backend.rag.evidence_gate.self_correction import SelfCorrectionStrategy
from backend.rag.reranker import RerankCompressor
from backend.rag.retrieval.retrievers import AdaptiveRetriever, ChunkLevelRetriever
from backend.shared.logger import logger

__all__ = [
    "AdaptiveRetriever",
    "ChunkLevelRetriever",
    "CitationFormatter",
    "DEFAULT_CONTEXTUALIZE_SYSTEM",
    "DEFAULT_DOCUMENT_TEMPLATE",
    "DEFAULT_QA_SYSTEM",
    "ENABLE_HISTORY_AWARE_RETRIEVAL",
    "ENABLE_TOKEN_STREAMING",
    "EVIDENCE_TOKEN_BUDGET",
    "EvidenceGateController",
    "RAGChain",
    "RerankCompressor",
    "SelfCorrectionStrategy",
    "_HISTORY_REWRITE_CUES",
    "_build_contextualize_prompt",
    "_build_document_prompt",
    "_build_qa_prompt",
    "_build_retrieval_span_metrics",
    "_llm_stream",
    "_mark_gate_degraded",
    "_needs_history_rewrite",
    "clear_context",
    "emit_stream_delta",
    "extract_chunk_reasoning",
    "extract_chunk_text",
    "get_context",
    "llm",
    "logger",
    "set_context",
]


class RAGChain:
    """LangChain 高层封装的 RAG 管道，整合三层记忆系统。"""

    def __init__(
        self,
        doc_db,
        vectordb,
        chunk_retriever,
        bm25,
        person_index: dict = None,
        memory_manager=None,
    ):
        self.doc_db = doc_db
        self.vectordb = vectordb
        self.chunk_retriever = chunk_retriever
        self.bm25 = bm25
        self.person_index = person_index or {}
        self._memory = memory_manager
        self._runtime_prompt_keys: list[str] = []
        self.formatter = CitationFormatter()
        self._chains_dirty = False
        self._last_embedding_stats = None

        self._build_retrievers()
        self._build_chains()
        self._register_prompt_reload_hooks()
        logger.info("LangChain RAG Chain 初始化完成")

    # ── 决策中间态：随 RequestContext 隔离（contextvars），防单例并发串扰 ──
    @property
    def _last_meta(self) -> dict:
        return get_context().meta

    @_last_meta.setter
    def _last_meta(self, value: dict) -> None:
        get_context().meta = value

    @property
    def _last_faithfulness(self):
        return get_context().faithfulness

    @_last_faithfulness.setter
    def _last_faithfulness(self, value) -> None:
        get_context().faithfulness = value

    @property
    def _last_query(self) -> str:
        return get_context().query

    @_last_query.setter
    def _last_query(self, value: str) -> None:
        get_context().query = value

    @property
    def _last_sources(self) -> list:
        return get_context().sources

    @_last_sources.setter
    def _last_sources(self, value: list) -> None:
        get_context().sources = list(value or [])

    @property
    def _last_citation_evidence(self) -> list:
        return get_context().citation_evidence

    @_last_citation_evidence.setter
    def _last_citation_evidence(self, value: list) -> None:
        get_context().citation_evidence = list(value or [])

    @property
    def gate(self):
        ctx = get_context()
        if ctx.gate is None:
            ctx.gate = EvidenceGateController()
        return ctx.gate

    @gate.setter
    def gate(self, value) -> None:
        get_context().gate = value

    @property
    def corrector(self):
        ctx = get_context()
        if ctx.corrector is None:
            ctx.corrector = SelfCorrectionStrategy()
        return ctx.corrector

    @corrector.setter
    def corrector(self, value) -> None:
        get_context().corrector = value

    # ── 构建器委托 ──
    def _build_retrievers(self):
        build_retrievers(self)

    def _build_chains(self):
        build_rag_chains(self)

    def _register_prompt_reload_hooks(self):
        register_prompt_reload_hooks(self)

    def _mark_chains_dirty(self):
        self._chains_dirty = True

    # ── 检索与 Gate 委托 ──
    def _gate_wrap_retrieve(self, payload):
        return gate_wrap_retrieve(self, payload)

    def _decision_from_injected(self, injected):
        return decision_from_injected(injected)

    def _run_evidence_gates(self, question: str, context_docs: list):
        return run_evidence_gates(self, question, context_docs)

    def _record_retrieval_events(self, ret_span, context_docs: list) -> None:
        record_retrieval_events(ret_span, context_docs)

    # ── 校验与评估委托 ──
    def _verify(self, result: dict, question: str, session_id: str = "default") -> str:
        return verify_answer(self, result, question, session_id)

    def _verify_claims(self, answer: str, context_docs: list) -> str | None:
        return verify_claims(answer, context_docs)

    def _evaluate(self, answer: str, context_docs: list) -> str:
        return evaluate_faithfulness(self, answer, context_docs)

    # ── 生命周期与调度委托 ──
    def _start(self, question: str, session_id: str):
        return start_trace(self, question, session_id)

    def _record_runtime_prompt_usage(self) -> None:
        record_runtime_prompt_usage(self)

    def _remember_turn(self, session_id: str, question: str, answer: str,
                       trace=None, persist_memory: bool = True) -> None:
        remember_turn(self, session_id, question, answer, trace, persist_memory)

    def _emit_embedding_stats(self, stats) -> None:
        emit_embedding_stats(stats)

    def _record_rag_metric(self, status: str) -> None:
        record_rag_metric(status)

    def _handle_llm_reject(self, meta, trace, question, session_id, t_total,
                           persist_memory: bool = True) -> str:
        return handle_llm_reject(self, meta, trace, question, session_id, t_total, persist_memory)

    def _reject(self, decision, layer: str, trace, t_total: float,
                self_correction_attempted: bool = False) -> str:
        return reject(self, decision, layer, trace, t_total, self_correction_attempted)

    def _finish(self, trace, answer: str, t_total: float):
        finish_response(self, trace, answer, t_total)

    def _finish_error(self, trace, t_total: float):
        finish_error(self, trace, t_total)

    @staticmethod
    def _end_root_span(trace, output: dict = None, metrics: dict = None,
                       status: str = "success"):
        end_root_span(trace, output, metrics, status)

    def _try_self_correct(self, original_decision, trace, question, session_id, t_total):
        return try_self_correct(self, original_decision, trace, question, session_id, t_total)

    def _prepare(self, question: str, session_id: str) -> list:
        return prepare_session(self, question, session_id)

    def _execute(self, question: str, chat_history: list) -> dict:
        return execute_chain(self, question, chat_history)

    def _respond(self, result, trace, question, session_id, t_total,
                 persist_memory: bool = True) -> str:
        return respond(self, result, trace, question, session_id, t_total, persist_memory)

    # ── 公共问答入口 ──
    def ask(self, question: str, session_id: str = "default",
            persist_memory: bool = True) -> str:
        """RAGChain 入口：线性 3 段 — prepare → execute → respond。"""
        if self._chains_dirty:
            self._build_chains()
            self._chains_dirty = False

        from backend.rag.embedding_singleton import (
            begin_embedding_request,
            end_embedding_request,
            report_embedding_request,
        )

        trace, t_total = self._start(question, session_id)
        begin_embedding_request()
        try:
            ctx = get_context()
            ctx.meta = {}
            ctx.faithfulness = None
            ctx.mq_triggered = False
            ctx.gate = EvidenceGateController()
            ctx.corrector = SelfCorrectionStrategy()
            set_context(ctx)
            chat_history = self._prepare(question, session_id)
            result = self._execute(question, chat_history)
            answer = self._respond(
                result, trace, question, session_id, t_total,
                persist_memory=persist_memory,
            )
            if ENABLE_TOKEN_STREAMING and answer:
                emit_stream_delta(answer)
            try:
                get_context().meta["trace_id"] = trace.id
            except Exception:  # noqa: BLE001
                pass
            from backend.observability.metrics import record_rag_stage
            record_rag_stage("total", (_time.time() - t_total) * 1000)
            return answer
        except Exception:
            self._finish_error(trace, t_total)
            from backend.observability.metrics import record_rag_stage
            record_rag_stage("total", (_time.time() - t_total) * 1000)
            raise
        finally:
            embedding_stats = end_embedding_request()
            if embedding_stats is not None:
                self._last_embedding_stats = embedding_stats
                report_embedding_request(embedding_stats)
                self._emit_embedding_stats(embedding_stats)
