"""Multi Query Retrieval — 自动触发 + LLM改写 + 质量过滤

need_multi_query() 是唯一入口，所有 MultiQuery 判断必须经过此函数。
"""

import contextvars
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, List

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import Field

from backend.config import (
    MULTI_QUERY_MODE as _DEFAULT_MODE, MULTI_QUERY_COUNT,
    MULTI_QUERY_TOP_K_PER, MULTI_QUERY_DEDUP,
    MULTI_QUERY_SIMILARITY, MULTI_QUERY_MIN_LENGTH,
    MULTI_QUERY_MAX_TOKENS,
)
from backend.rag.context import get_context
from backend.prompts.service import prompt_service

from backend.shared.logger import logger

# 运行时模式（可通过 API POST /llm/multiquery 动态切换）
_mq_mode: str = _DEFAULT_MODE

# 改写输出的 token 下限。历史值 200 被实测证明**不足**：2026-10-09 从
# trace_store 统计 154 次改写，151 次 completion_tokens 恰好等于 200 且
# 只解析出 1 个变体（145/155/191 tokens 的 3 次全部正常出 3 个即 98% 撞上限）。
# 根因是主模型为思考型（doubao-seed-2.0-mini），reasoning 计入
# completion_tokens，思考吃掉大部分预算后正文被截断——多查询因此长期
# 静默退化为单查询，还白付 ~2.3s 串行等待。
# 修复=角色化（改走 non-thinking 轻模型）+ 上限兜底到 512，两者缺一不可：
# 只调角色时若管理员仍绑定思考型模型，512 仍会被思考吃光。
# RC-024 在 512 completion tokens 再次截断，保留角色/温度，仅提高输出下限。
_REWRITE_MAX_TOKENS_FLOOR = 1024

# 请求级有效查询列表的私有元数据键（Phase B，PR #52）。
# 只挂在本次请求首个候选 Document 的 metadata 上，随 ContextualCompressionRetriever
# 的同一次 invoke 传给 RerankCompressor；Rerank 在本层外层（装配顺序不变），
# 因此压缩器拿到的正是 MultiQuery 合并后的同一批候选。前缀 "_runtime_" 表示
# 非持久化字段：候选来自检索缓存副本（retrievers.py 返回 copy），不会写回
# Chroma/PG 元数据；Trace/引用导出均按显式键读取，不含该字段。
RUNTIME_MQ_QUERIES_KEY = "_runtime_mq_queries"


@dataclass
class _RewriteAttempt:
    """跨调用方与工作线程共享的改写诊断状态。"""

    attempt_id: str
    started_at: float
    span: Any
    metrics: dict[str, Any] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    caller_timed_out: bool = False
    worker_finished: bool = False
    late_span_recorded: bool = False

    def update(self, **values: Any) -> None:
        with self.lock:
            self.metrics.update(values)

    def add_duration(self, name: str, duration_ms: float) -> None:
        with self.lock:
            previous = self.metrics.get(name, 0.0)
            if not isinstance(previous, (int, float)):
                previous = 0.0
            self.metrics[name] = round(
                float(previous) + max(duration_ms, 0.0), 1,
            )

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "rewrite_attempt_id": self.attempt_id,
                "rewrite_total_ms": round(
                    (time.monotonic() - self.started_at) * 1000, 1,
                ),
                "provider_ttfb_ms": "unavailable",
                **self.metrics,
            }

    def mark_caller_timeout(self) -> bool:
        """标记业务等待超时；返回是否需要由调用方补记迟到 span。"""
        with self.lock:
            self.caller_timed_out = True
            if self.worker_finished and not self.late_span_recorded:
                self.late_span_recorded = True
                return True
            return False

    def caller_timeout_snapshot(self, **values: Any) -> tuple[dict[str, Any], bool]:
        """原子冻结调用方 timeout 结论，避免工作线程迟到写入覆盖主 span。"""
        with self.lock:
            self.caller_timed_out = True
            self.metrics.update(values)
            if self.worker_finished:
                self.metrics["worker_state"] = "finished"
            write_late_span = False
            if self.worker_finished and not self.late_span_recorded:
                self.late_span_recorded = True
                write_late_span = True
            return self.snapshot(), write_late_span

    def mark_worker_finished(self) -> bool:
        """标记线程任务结束；返回是否需要由工作线程补记迟到 span。"""
        with self.lock:
            self.worker_finished = True
            if self.caller_timed_out and not self.late_span_recorded:
                self.late_span_recorded = True
                return True
            return False


_rewrite_attempt_var: contextvars.ContextVar[_RewriteAttempt | None] = (
    contextvars.ContextVar("rag_multi_query_rewrite_attempt", default=None)
)


def _current_rewrite_attempt() -> _RewriteAttempt | None:
    return _rewrite_attempt_var.get()


def _record_proxy_diagnostic(stage: str, values: dict[str, Any]) -> None:
    attempt = _current_rewrite_attempt()
    if attempt is None:
        return
    if stage.endswith("_ms"):
        attempt.add_duration(stage, float(values.get("duration_ms", 0.0)))
        if stage == "budget_reserve_ms":
            attempt.update(
                reservation_id=values.get("reservation_id") or "",
            )
        return
    if stage == "provider_call_started":
        attempt.update(
            provider_call_started=True,
            internal_call_id=values.get("call_id") or "",
            reservation_id=values.get("reservation_id") or "",
        )
        with attempt.lock:
            attempt.metrics["provider_attempt_count"] = (
                int(attempt.metrics.get("provider_attempt_count", 0)) + 1
            )
            ids = list(attempt.metrics.get("provider_call_ids", []))
            ids.append(str(values.get("call_id") or ""))
            attempt.metrics["provider_call_ids"] = ids
    elif stage == "provider_completed":
        attempt.add_duration("provider_call_ms", values.get("duration_ms", 0))
        attempt.add_duration("provider_response_ms", values.get("duration_ms", 0))
        attempt.update(
            provider_completed=True,
            provider_outcome="completed",
            internal_call_id=values.get("call_id") or "",
            reservation_id=values.get("reservation_id") or "",
        )
    elif stage == "provider_error":
        attempt.add_duration("provider_call_ms", values.get("duration_ms", 0))
        attempt.update(
            provider_client_wait_ended=True,
            provider_completion="unknown",
            provider_outcome="error",
            internal_call_id=values.get("call_id") or "",
            provider_error_type=values.get("error_type", "unknown"),
            reservation_id=values.get("reservation_id") or "",
        )
        if values.get("timeout"):
            attempt.update(timeout_kind="provider_io_timeout")
    elif stage == "provider_not_sent":
        with attempt.lock:
            not_sent = list(attempt.metrics.get("provider_call_not_sent_ids", []))
            not_sent.append(str(values.get("call_id") or ""))
            attempt.metrics["provider_call_not_sent_ids"] = not_sent
            sent_ids = set(attempt.metrics.get("provider_call_ids", []))
            attempt.metrics["provider_call_started"] = bool(sent_ids - set(not_sent))
            attempt.metrics["provider_error_type"] = values.get(
                "error_type", "unknown",
            )
    elif stage == "call_identity":
        attempt.update(
            model=values.get("model") or "",
            role=values.get("role") or "",
            provider=values.get("provider") or "",
        )
    elif stage == "usage_unavailable":
        attempt.update(
            usage_status="unavailable",
            reservation_id=values.get("reservation_id") or "",
            settlement_status=values.get("settlement_status", "needs_review"),
        )
    elif stage == "usage_accounting_finished":
        previous_status = attempt.snapshot().get("settlement_status", "pending")
        reported_status = values.get("settlement_status")
        status = (
            reported_status
            if reported_status and reported_status != "unknown"
            else previous_status
            if previous_status != "pending"
            else "settlement_attempted"
            if values.get("usage_received")
            else "needs_review"
        )
        if values.get("cost_status") == "unpriced" and status in {
            "settled", "settlement_attempted",
        }:
            status = "needs_review"
        reservation_id = (
            values.get("reservation_id")
            or attempt.snapshot().get("reservation_id")
            or ""
        )
        attempt.update(
            usage_status=("received" if values.get("usage_received") else "unavailable"),
            settlement_status=status,
            reservation_id=reservation_id,
            pricing_source=values.get("pricing_source", "unknown"),
            price_version=values.get("price_version", ""),
            cost_status=values.get("cost_status", "unknown"),
        )
    elif stage == "usage_ledger_write_finished":
        attempt.update(
            usage_ledger_status=(
                "written" if values.get("written") else "write_failed"
            ),
        )


def _record_late_rewrite_span(attempt: _RewriteAttempt) -> None:
    """迟到工作线程只追加关联 span，不改写调用方的 timeout span。"""
    try:
        from backend.observability.tracer import trace_collector

        late = trace_collector.start_span(
            "query_rewrite_late",
            parent_id=getattr(attempt.span, "span_id", None),
            name="改写迟到结果",
            type="llm_call",
        )
        metrics = attempt.snapshot()
        metrics["caller_outcome"] = "timeout"
        metrics["worker_outcome"] = "finished"
        metrics["provider_completed_late"] = bool(
            metrics.get("provider_completed")
        )
        trace_collector.end_span(
            late, metrics=metrics, status="completed_late",
        )
    except Exception:  # noqa: BLE001 — Trace 旁路不得反噬工作线程
        logger.debug("[MultiQuery] 迟到结果 Trace 记录失败", exc_info=True)


def _resolve_rewrite_llm():
    """返回改写专用的治理化 LLM。

    空角色按注册表规则继承 main。必须使用公共 Proxy 入口，使请求经过限流、
    预算预占/结算、韧性处理和用量审计。没有可用模型时由改写调用方降级为
    原始 query，不能再绕回裸全局 ``llm``。
    """
    from backend.infra.llm.proxy import bind_llm_for_role

    return bind_llm_for_role(
        "multi_query",
        temperature=0,
        max_tokens=_rewrite_max_tokens(),
    )


def _rewrite_max_tokens() -> int:
    """改写 token 上限：配置值与安全下限取大（防思考型模型截断变体）。"""
    try:
        return max(int(MULTI_QUERY_MAX_TOKENS), _REWRITE_MAX_TOKENS_FLOOR)
    except (TypeError, ValueError):
        return _REWRITE_MAX_TOKENS_FLOOR


def get_mq_mode() -> str:
    return _mq_mode


def set_mq_mode(mode: str):
    global _mq_mode
    _mq_mode = mode


# =====================================================
# 唯一入口 — 所有 MultiQuery 判断必须走这里
# =====================================================

def need_multi_query(query: str) -> tuple[bool, str]:
    """判断是否需要 MultiQuery。所有入口统一经过此函数。

    mode=off     → 直接关闭
    mode=always  → 直接开启
    mode=on      → 兼容旧写法，同 always
    mode=auto    → 由三层分类器 _classify_query_tier() 决定：
                   仅 hybrid_multi_query 触发，vector_only/hybrid 不触发。
    """
    mode = _mq_mode

    if mode == "off":
        return False, "off"
    if mode in ("always", "on"):
        return True, mode
    # auto → 统一由三层分类器决策
    try:
        from backend.rag.retrieval.hybrid import _classify_query_tier
        tier = _classify_query_tier(query)
        if tier == "hybrid_multi_query":
            return True, f"三层路由: {tier}"
        return False, f"三层路由: {tier}"
    except Exception:
        # 分类器不可用时回退到本地规则
        return _is_complex(query)


# =====================================================
# 复杂度检测（仅 auto 模式使用）
# =====================================================

# ⭐ 单一事实来源（治理 B）：复杂度信号表收归 hybrid.py 的 COMPLEX_PATTERNS
#    （三层路由分类器是主消费者），本模块只引用。历史上两份清单漂移
#    （本表曾有 流程/步骤 而 hybrid 表没有，反向亦然），导致分类行为分叉。
# 导入方向安全：hybrid 模块级不依赖本模块（need_multi_query 对 hybrid 的
# 引用在函数内惰性执行）。
from backend.rag.retrieval import hybrid as hybrid_simple  # noqa: E402
from backend.rag.retrieval.hybrid import (  # noqa: E402
    COMPLEX_PATTERNS,
    COMPLEX_STRONG_PATTERNS,
    COMPLEX_WEAK_PATTERNS,
)

# 业务关键词（2026-08-11 P2 多路融合）：自动触发 MultiQuery 改写
# 解决"差评怎么处理"等业务查询不会触发改写的问题。
# 注意保持精简：命中即触发 1 次 LLM 改写 + N 路检索，过宽会放大延迟与成本
BUSINESS_KEYWORDS = [
    "差评", "投诉", "退款", "退货", "换货", "售后",
    "合规", "审核", "罚款", "违规", "处罚",
    "上架", "下架", "Listing", "广告", "投放",
    "缺货", "断货", "调拨", "滞销", "库存", "FBA",
    "毛利", "利润", "客单价", "转化率",
    "SKU", "SPU",
]

# R1-B（2026-10-12）：简单事实问句清单与判定已收归 hybrid.py 单一事实来源
# （is_simple_fact_query / SIMPLE_FACT_PATTERNS / SIMPLE_PREFIXES）。此处
# 仅做兼容再导出，禁止在本模块另维护分表——历史上兜底把强信号排在简单
# 事实豁免之前，「退款审核时间是多少？」被"退款/审核"当业务关键词误判成
# 复杂问句，白付 1 次 LLM 改写 + 3 路检索。
SIMPLE_PREFIXES = hybrid_simple.SIMPLE_PREFIXES
SIMPLE_FACT_PATTERNS = hybrid_simple.SIMPLE_FACT_PATTERNS


def _is_complex(query: str) -> tuple[bool, str]:
    """兜底判定：复用唯一判定入口 _classify_query_tier 的规则。

    P1-D 修正（2026-10-12）：旧兜底自持一套"业务关键词"规则，与主分类器
    真实分叉（「审核时间是多久」兜底判复杂、主分类器判 hybrid；
    「差评怎么处理」兜底判复杂、主分类器判 hybrid）——同一问题结论取决于
    分类器是否超时。现在只保留一份规则：主入口可用时用主入口，主入口抛错
    （本函数被调用的前提）时**保守不改写**：
      - vector_only/hybrid 双路检索仍由底层保底完成，不会丢召回；
      - 只有 MQ 改写被跳过，属于既有 Phase 4 降级语义（宁少一次改写，
        不多烧一次 LLM + 3 路检索）。
    """
    try:
        tier = hybrid_simple._classify_query_tier(query)
    except Exception as exc:  # noqa: BLE001 — 分类器本身不可用
        logger.warning(f"[MultiQuery] 分类器不可用，保守跳过改写: {exc}")
        return False, "classifier_unavailable"
    if tier == "hybrid_multi_query":
        return True, f"三层路由: {tier}"
    return False, f"三层路由: {tier}"


# =====================================================
# Query Rewrite: Parse → Normalize → Deduplicate → Limit
# =====================================================

def _finish_rewrite_attempt(
    attempt: _RewriteAttempt,
    status: str,
    metrics: dict[str, Any] | None = None,
) -> None:
    """由调用方单次收口主改写 span，迟到工作线程另建关联 span。"""
    from backend.observability.tracer import trace_collector

    metrics = metrics or attempt.snapshot()
    query_count = int(metrics.get("query_count", 1))
    started = time.monotonic()
    trace_collector.end_span(
        attempt.span,
        metrics=metrics,
        output={"query_count": query_count},
        status=status,
    )
    # end_span 同步通知监听器；把测量结果写回最终 TraceRecord 供落库查询。
    attempt.span.metrics["trace_finalize_ms"] = round(
        (time.monotonic() - started) * 1000, 1,
    )


def _rewrite_with_budget(question: str) -> list[str]:
    """在共享 deadline 内改写；超时保留原问题并显式区分调用方与 Provider。"""
    from concurrent.futures import TimeoutError as _FutureTimeout

    from backend.config import model_roles
    from backend.config.rag import (
        RAG_MULTI_QUERY_REWRITE_TIMEOUT_MS,
        RAG_RETRIEVAL_DEADLINE_MS,
    )
    from backend.infra.llm import proxy as llm_proxy
    from backend.infra.thread_pools import submit_rag_task
    from backend.observability.tracer import trace_collector
    from backend.rag.retrieval_budget import (
        current_budget,
        remaining_ms as retrieval_remaining_ms,
    )

    started = time.monotonic()
    attempt_id = uuid.uuid4().hex
    span = trace_collector.start_span(
        "query_rewrite", name="LLM 改写", type="llm_call",
    )
    attempt = _RewriteAttempt(attempt_id, started, span)
    budget = current_budget()
    deadline_before = retrieval_remaining_ms(RAG_RETRIEVAL_DEADLINE_MS)
    try:
        from backend.infra.llm.budget import current_request_id
        from backend.observability.tracer import current_trace_context

        trace_id, _session_id = current_trace_context()
        request_id = current_request_id()
    except Exception:
        trace_id, request_id = "", ""
    attempt.update(
        prompt_key="rag.multi_query",
        prompt_status="not_rendered",
        queue_wait_ms="unavailable",
        worker_start_ms="unavailable",
        model_resolve_ms="unavailable",
        prompt_render_ms="unavailable",
        price_preflight_ms="unavailable",
        budget_reserve_ms="unavailable",
        provider_call_ms="unavailable",
        provider_response_ms="unavailable",
        response_parse_ms="unavailable",
        trace_finalize_ms="pending",
        retrieval_deadline_ms=round(float(RAG_RETRIEVAL_DEADLINE_MS), 1),
        shared_deadline_active=budget is not None,
        deadline_remaining_before_ms=(
            round(max(deadline_before, 0.0), 1)
            if deadline_before is not None else "unavailable"
        ),
        rewrite_timeout_configured_ms=round(
            float(RAG_MULTI_QUERY_REWRITE_TIMEOUT_MS), 1,
        ),
        rewrite_timeout_source=(
            "environment:RAG_MULTI_QUERY_REWRITE_TIMEOUT_MS"
            if "RAG_MULTI_QUERY_REWRITE_TIMEOUT_MS" in os.environ
            else "code_default"
        ),
        role="multi_query",
        request_id=request_id or "",
        trace_id=trace_id or "",
        provider_ttfb_ms="unavailable",
        provider_completion="not_started",
        internal_call_id="",
        provider_call_ids=[],
        provider_request_id="",
        usage_ledger_status="unavailable",
        finish_reason="unknown",
        provider_call_started=False,
        provider_completed=False,
        provider_cancel_acknowledged=False,
        usage_status="pending",
        settlement_status="pending",
        timeout_kind="none",
        query_count=1,
        fallback="original_query",
    )

    role_started = time.monotonic()
    try:
        policy = model_roles.resolve_runtime_policy("multi_query")
        role_timeout_ms = max(200.0, float(policy.timeout_seconds) * 1000)
        role_timeout_source = (
            "database_role_policy"
            if model_roles.has_db_policy_value("multi_query", "timeout_seconds")
            else "code_default_role_policy"
        )
    except Exception:
        role_timeout_ms = float(RAG_MULTI_QUERY_REWRITE_TIMEOUT_MS)
        role_timeout_source = "role_policy_resolution_failed"
    attempt.add_duration("role_resolve_ms", (time.monotonic() - role_started) * 1000)
    attempt.update(
        role_timeout_ms=round(role_timeout_ms, 1),
        role_timeout_source=role_timeout_source,
    )

    deadline_after_role = retrieval_remaining_ms(RAG_RETRIEVAL_DEADLINE_MS)
    configured_ms = max(200.0, float(RAG_MULTI_QUERY_REWRITE_TIMEOUT_MS))
    role_capped_ms = min(configured_ms, role_timeout_ms)
    effective_ms = min(
        role_capped_ms,
        max(deadline_after_role, 0.0)
        if deadline_after_role is not None else role_capped_ms,
    )
    if deadline_after_role is not None and deadline_after_role < role_capped_ms:
        timeout_source = "shared_retrieval_deadline"
    elif configured_ms <= role_timeout_ms:
        timeout_source = attempt.metrics["rewrite_timeout_source"]
    else:
        timeout_source = role_timeout_source
    attempt.update(
        rewrite_timeout_effective_ms=round(effective_ms, 1),
        rewrite_timeout_source=timeout_source,
        deadline_remaining_after_ms=(
            round(max(deadline_after_role, 0.0), 1)
            if deadline_after_role is not None else "unavailable"
        ),
    )

    if effective_ms < 300:
        attempt.update(
            rewrite_status="degraded",
            degrade="budget_insufficient",
            finish_reason="not_called",
            provider_outcome="not_sent",
            provider_completion="not_started",
            usage_status="not_requested",
            settlement_status="not_reserved",
            timeout_kind=(
                "total_deadline_expired"
                if deadline_after_role is not None and deadline_after_role <= 0
                else "shared_deadline_insufficient"
            ),
        )
        _finish_rewrite_attempt(attempt, "skipped")
        logger.info(
            "[MultiQuery] 剩余共享预算不足(%.0fms)，跳过改写用原始 query",
            effective_ms,
        )
        return [question]

    cancel_event = threading.Event()
    request_context = contextvars.copy_context()
    submitted_at = time.monotonic()

    def _rewrite_worker() -> list[str]:
        worker_started = time.monotonic()
        attempt.add_duration("queue_wait_ms", (worker_started - submitted_at) * 1000)
        attempt.update(
            worker_start_ms=round(
                (worker_started - attempt.started_at) * 1000, 1,
            ),
        )
        token = _rewrite_attempt_var.set(attempt)
        try:
            return llm_proxy.run_with_role_cancellation(
                cancel_event, _rewrite, question,
            )
        finally:
            _rewrite_attempt_var.reset(token)
            if attempt.mark_worker_finished():
                _record_late_rewrite_span(attempt)

    try:
        future = submit_rag_task(
            "multi_query_rewrite",
            request_context.run,
            _rewrite_worker,
        )
    except Exception as exc:
        attempt.update(
            rewrite_status="degraded",
            degrade="worker_submit_failed",
            error_type=type(exc).__name__,
            timeout_kind="worker_start_failed",
        )
        _finish_rewrite_attempt(attempt, "error")
        return [question]

    try:
        queries = future.result(timeout=effective_ms / 1000)
        deadline_after = retrieval_remaining_ms(RAG_RETRIEVAL_DEADLINE_MS)
        if deadline_after is not None and deadline_after <= 0:
            attempt.update(
                rewrite_status="degraded",
                degrade="shared_deadline_expired",
                timeout_kind="total_deadline_expired",
                deadline_remaining_after_ms=0.0,
                query_count=1,
            )
            _finish_rewrite_attempt(attempt, "timeout")
            return [question]
        attempt.update(
            deadline_remaining_after_ms=(
                round(max(deadline_after, 0.0), 1)
                if deadline_after is not None else "unavailable"
            ),
        )
        status = str(attempt.snapshot().get("rewrite_status", "success"))
        _finish_rewrite_attempt(attempt, status)
        return queries
    except _FutureTimeout:
        deadline_after = retrieval_remaining_ms(RAG_RETRIEVAL_DEADLINE_MS)
        timeout_kind = (
            "total_deadline_expired"
            if timeout_source == "shared_retrieval_deadline"
            or (deadline_after is not None and deadline_after <= 0)
            else "caller_subbudget_timeout"
        )
        cancel_event.set()
        cancel_accepted = future.cancel()
        if cancel_accepted:
            attempt.update(
                queue_wait_ms="unavailable",
                worker_start_ms="unavailable",
            )
        timeout_metrics, write_late_span = attempt.caller_timeout_snapshot(
            rewrite_status="degraded",
            degrade="rewrite_timeout",
            timeout_kind=timeout_kind,
            caller_timeout=True,
            caller_cancel_requested=True,
            future_cancel_accepted=bool(cancel_accepted),
            provider_cancel_acknowledged=False,
            worker_state=(
                "cancelled_before_start" if cancel_accepted
                else "running"
            ),
            fallback="original_query",
            query_count=1,
            deadline_remaining_after_ms=(
                round(max(deadline_after, 0.0), 1)
                if deadline_after is not None else "unavailable"
            ),
        )
        _finish_rewrite_attempt(attempt, "timeout", metrics=timeout_metrics)
        if write_late_span:
            _record_late_rewrite_span(attempt)
        logger.warning(
            "[MultiQuery] 改写等待超时(%.0fms，%s)，回退原始 query",
            effective_ms, timeout_kind,
        )
        return [question]
    except Exception as exc:
        attempt.update(
            rewrite_status="degraded",
            degrade="rewrite_worker_error",
            error_type=type(exc).__name__,
            fallback="original_query",
            query_count=1,
        )
        _finish_rewrite_attempt(attempt, "error")
        return [question]


def _safe_provider_request_id(result: Any) -> str:
    """只保留响应中的短 request ID，不记录任意 Header 或响应正文。"""
    metadata = getattr(result, "response_metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    value = (
        metadata.get("provider_request_id")
        or metadata.get("request_id")
        or metadata.get("x_request_id")
        or getattr(result, "id", "")
    )
    candidate = str(value or "")[:128]
    return candidate if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", candidate) else ""


def _rewrite(question: str) -> list[str]:
    """调用治理 Proxy 改写查询；Trace 仅记录归因和数量，不写 Prompt 正文。"""
    from backend.observability.tracer import trace_collector

    attempt = _current_rewrite_attempt()
    owns_attempt = attempt is None
    token = None
    if attempt is None:
        span = trace_collector.start_span(
            "query_rewrite", name="LLM 改写", type="llm_call",
        )
        attempt = _RewriteAttempt(
            uuid.uuid4().hex, time.monotonic(), span,
            metrics={
                "prompt_key": "rag.multi_query",
                "prompt_status": "not_rendered",
                "prompt_render_ms": "unavailable",
                "model_resolve_ms": "unavailable",
                "price_preflight_ms": "unavailable",
                "provider_call_ms": "unavailable",
                "provider_response_ms": "unavailable",
                "response_parse_ms": "unavailable",
                "trace_finalize_ms": "pending",
                "role": "multi_query",
                "provider_ttfb_ms": "unavailable",
                "provider_completion": "not_started",
                "usage_ledger_status": "unavailable",
                "finish_reason": "unknown",
                "query_count": 1,
            },
        )
        token = _rewrite_attempt_var.set(attempt)

    attempt.update(max_tokens=_rewrite_max_tokens())
    try:
        from langchain_core.messages import HumanMessage
        from backend.config import model_roles
        from backend.infra.llm import proxy as llm_proxy

        prompt_started = time.monotonic()
        rendered = prompt_service.render_sync(
            "rag.multi_query",
            count=MULTI_QUERY_COUNT,
            question=question,
        )
        attempt.add_duration(
            "prompt_render_ms", (time.monotonic() - prompt_started) * 1000,
        )
        # 共享状态在 Provider 调用前即写入，等待超时的调用方可读取真实归因。
        attempt.update(
            prompt_key=getattr(rendered, "key", "rag.multi_query"),
            prompt_version=getattr(rendered, "version", None),
            prompt_source=getattr(rendered, "source", "unknown"),
            prompt_hash=getattr(rendered, "template_hash", "") or "",
            prompt_status="rendered",
        )
        messages = [HumanMessage(content=rendered.text)]

        model_started = time.monotonic()
        model_name = str(
            model_roles.resolve_runtime_name("multi_query") or ""
        ).strip()
        if not model_name:
            model_name = llm_proxy.get_active_model_name()
        model_source = model_roles.resolve_effective("multi_query").get("source", "unknown")
        provider = llm_proxy._get_provider_for(model_name)
        attempt.add_duration(
            "model_resolve_ms", (time.monotonic() - model_started) * 1000,
        )
        attempt.update(
            model=model_name,
            model_source=str(model_source),
            provider=provider,
            role="multi_query",
        )

        preflight_started = time.monotonic()
        llm_proxy.enforce_model_input_price_tier(
            (messages,), model_name=model_name,
        )
        attempt.add_duration(
            "price_preflight_ms", (time.monotonic() - preflight_started) * 1000,
        )

        llm = _resolve_rewrite_llm()
        with llm_proxy.scoped_call_diagnostics(_record_proxy_diagnostic):
            result = llm.invoke(
                messages,
                max_tokens=_rewrite_max_tokens(),
            )

        parse_started = time.monotonic()
        if hasattr(result, "content"):
            raw = str(result.content)
        elif isinstance(result, str):
            raw = result
        else:
            raise TypeError("rewrite_response_type")

        metadata = getattr(result, "response_metadata", None)
        metadata = metadata if isinstance(metadata, dict) else {}
        finish_reason = metadata.get("finish_reason") or metadata.get("stop_reason")
        if finish_reason in (
            "stop", "length", "max_tokens", "end_turn", "stop_sequence",
        ):
            attempt.update(finish_reason=finish_reason)
        else:
            attempt.update(finish_reason="unknown")
        tokens = trace_collector.parse_tokens(result)
        if isinstance(tokens, dict):
            safe_tokens = {
                key: value for key, value in tokens.items()
                if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
                and isinstance(value, (int, float)) and value > 0
            }
            attempt.update(**safe_tokens)
        if not attempt.snapshot().get("usage_status") == "received":
            attempt.update(usage_status="unavailable")
        provider_request_id = _safe_provider_request_id(result)
        if provider_request_id:
            attempt.update(provider_request_id=provider_request_id)

        hit_limit = (
            finish_reason in ("length", "max_tokens")
            or int((tokens or {}).get("completion_tokens", 0) or 0)
            >= _rewrite_max_tokens()
        )
        lines = _dedup(_normalize(_parse(raw)), question)
        before = len(lines)
        lines = [line for line in lines if _plausible_variant(line, question)]
        if before and len(lines) < before:
            logger.info(
                "[MultiQuery] 合法性过滤剔除 %d 个异常变体",
                before - len(lines),
            )
        if not lines:
            lines = [question]
        lines = _limit(lines)
        if hit_limit:
            lines = [question]
        truncated = hit_limit or len(lines) < MULTI_QUERY_COUNT
        status = (
            "degraded" if len(lines) <= 1
            else "partial" if truncated else "success"
        )
        attempt.update(
            query_count=len(lines),
            rewrite_status=status,
            rewrite_truncated=truncated,
            rewrite_ms=round((time.monotonic() - attempt.started_at) * 1000, 1),
            fallback="original_query" if len(lines) <= 1 else "",
        )
        if truncated:
            attempt.update(degrade="rewrite_truncated")
        if not hit_limit and len(lines) < MULTI_QUERY_COUNT:
            attempt.update(
                degrade="rewrite_truncated",
                truncation_reason="incomplete_query_set",
            )
        attempt.add_duration(
            "response_parse_ms", (time.monotonic() - parse_started) * 1000,
        )
        logger.info("[MultiQuery] 改写返回 %d 条有效查询", len(lines))
        return lines
    except Exception as exc:
        timeout_error = (
            isinstance(exc, TimeoutError)
            or "timeout" in type(exc).__name__.lower()
        )
        attempt.update(
            rewrite_status="degraded",
            degrade="provider_io_timeout" if timeout_error else "rewrite_error",
            timeout_kind="provider_io_timeout" if timeout_error else "none",
            error_type=type(exc).__name__,
            fallback="original_query",
            query_count=1,
            rewrite_truncated=False,
            rewrite_ms=round((time.monotonic() - attempt.started_at) * 1000, 1),
        )
        logger.warning(
            "[MultiQuery] Rewrite 失败（%s），回退到原始 query",
            type(exc).__name__,
        )
        return [question]
    finally:
        if owns_attempt:
            attempt.update(
                rewrite_total_ms=round(
                    (time.monotonic() - attempt.started_at) * 1000, 1,
                ),
            )
            _finish_rewrite_attempt(
                attempt,
                str(attempt.snapshot().get("rewrite_status", "error")),
            )
            _rewrite_attempt_var.reset(token)


# ── 变体合法性（D-11）────────────────────────────────
# 实测缺陷：doubao 思考型模型偶发把检索内容/上下文原文当作改写查询输出
# （数百字陈述句）→ 用 chunk 原文再检索 → 自匹配 → EvidenceGate 拒答。
# 双门过滤：长度不超过 max(原问题 2 倍, 40 字) 且非空。
_PLAUSIBLE_MIN_LEN = 4


def _plausible_variant(variant: str, question: str) -> bool:
    v = (variant or "").strip()
    if len(v) < _PLAUSIBLE_MIN_LEN:
        return False
    return len(v) <= max(len(question) * 2, 40)


# ── Parse ──────────────────────────────────────────

_RE_NUMBERING = re.compile(r"^\d+[\.\)、]\s*")           # 1. 2) 3、
_RE_BULLET = re.compile(r"^[-*•]\s*")                     # - * •
_RE_CJK_NUM = re.compile(r"^[（(][一二三四五六七八九十]+[）)]\s*")  # （一）（二）

def _parse(raw: str) -> list[str]:
    """解析 LLM 返回内容为查询列表"""
    lines = []
    for line in raw.strip().split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        lines.append(stripped)
    return lines


# ── Normalize ──────────────────────────────────────

def _normalize(lines: list[str]) -> list[str]:
    """统一文本格式：去编号、去列表符号、去首尾空格、合并空格"""
    result = []
    for line in lines:
        # 去掉中文编号 (一) (二)
        line = _RE_CJK_NUM.sub("", line)
        # 去掉数字编号 1. 2) 3、
        line = _RE_NUMBERING.sub("", line)
        # 去掉列表符号 - * •
        line = _RE_BULLET.sub("", line)
        # 合并连续空格
        line = re.sub(r"\s+", " ", line)
        # 去首尾空格
        line = line.strip()
        if _is_label_only(line):
            # 模板要求"每行一条纯文本查询，不要编号"，模型仍可能回显
            # "改写结果："/"以下是查询：" 这类标签行；它不是查询，若参与
            # 计数会挤占 _limit 名额并污染检索，统一在此丢弃。
            continue
        if line:
            result.append(line)
    return result


_LABEL_ONLY_MAX_LEN = 12


def _is_label_only(line: str) -> bool:
    """标签行（如 "改写结果："）判定：短且以冒号结尾。"""
    return len(line) <= _LABEL_ONLY_MAX_LEN and line.endswith(("：", ":"))


# ── Deduplicate ────────────────────────────────────

def _word_set(text: str) -> set[str]:
    """jieba 词级切分；不可用时回退字符级。"""
    try:
        import jieba
        return {w for w in jieba.cut(text) if w.strip()}
    except Exception:
        return set(text)


def _similarity(original: str, line: str) -> float:
    """综合相似度 = max(字符级 Jaccard, 词级 Jaccard, 词集最小包含度)。

    字符级对中文短问句区分度不足（如仅添加虚词"的"恰好卡在阈值上）；
    词集最小包含度 |A∩B|/min(|A|,|B|) 可识别"原查询仅加虚词/换词序"的变体。
    """
    o_set, l_set = set(original), set(line)
    union = o_set | l_set
    char_jaccard = len(o_set & l_set) / len(union) if union else 0.0

    o_words, l_words = _word_set(original), _word_set(line)
    w_union = o_words | l_words
    word_jaccard = len(o_words & l_words) / len(w_union) if w_union else 0.0
    containment = (
        len(o_words & l_words) / min(len(o_words), len(l_words))
        if o_words and l_words else 0.0
    )
    return max(char_jaccard, word_jaccard, containment)


def _dedup(lines: list[str], original: str = "") -> list[str]:
    """去重：完全重复 + 词级综合相似度（字符 Jaccard/词 Jaccard/词集包含度）"""
    # 确保原始查询在第一位
    result = [original] if original else []
    seen = {original} if original else set()

    for line in lines:
        if not line:
            continue
        # 完全重复
        if line in seen:
            logger.debug(f"[MultiQuery] 完全重复: {line}")
            continue
        if not MULTI_QUERY_DEDUP:
            seen.add(line)
            result.append(line)
            continue
        # 最小长度
        if len(line) < MULTI_QUERY_MIN_LENGTH:
            logger.debug(f"[MultiQuery] 过短: {line}")
            continue
        # 综合相似度去重（词级 + 字符级 + 包含度）
        if original:
            sim = _similarity(original, line)
            if sim > MULTI_QUERY_SIMILARITY:
                logger.debug(f"[MultiQuery] 相似度重复({sim:.2f}): {line}")
                continue
        seen.add(line)
        result.append(line)
    return result


# ── Limit ──────────────────────────────────────────

def _limit(lines: list[str]) -> list[str]:
    """限制最终查询数量"""
    return lines[:MULTI_QUERY_COUNT]


# =====================================================
# LangChain Retriever
# =====================================================

class MultiQueryRetriever(BaseRetriever):
    """根据 need_multi_query() 判断是否启用多查询。"""

    base_retriever: Any = Field(description="底层 ChunkLevelRetriever")

    class Config:
        arbitrary_types_allowed = True

    def __init__(self, base_retriever):
        super().__init__(base_retriever=base_retriever)
        # 触发状态随 RequestContext 隔离（并发下同一 retriever 实例
        # 被多请求共享，实例字段会串扰 trace 报告）
        self._last_triggered = False
        self._last_reason = ""
        self._last_variants = 1
        self._last_filtered = 1

    @property
    def _last_triggered(self) -> bool:
        return get_context().mq_triggered

    @_last_triggered.setter
    def _last_triggered(self, value: bool) -> None:
        get_context().mq_triggered = value

    def _get_relevant_documents(self, query: str, *, run_manager=None) -> list[Document]:
        use, reason = need_multi_query(query)
        self._last_triggered = use
        self._last_reason = reason
        self._last_variants = 1
        self._last_filtered = 1

        if not use:
            logger.info(f"[MultiQuery] {reason}: {query[:30]}")
            from backend.observability.tracer import trace_collector
            span = trace_collector.start_span("query_rewrite", name="LLM改写")
            trace_collector.end_span(span,
                                     metrics={"variants": 0}, status="skipped")
            return self.base_retriever.invoke(query)

        queries = _rewrite_with_budget(query)
        self._last_variants = len(queries)
        self._last_filtered = len(queries)

        # LLM 改写失败（返回仅含原始 query）→ 降级到 hybrid 单路检索
        if len(queries) <= 1:
            logger.warning(
                f"[MultiQuery] LLM 改写失败，降级到 hybrid 单路检索: {query[:30]}"
            )
            return self.base_retriever.invoke(query)

        from concurrent.futures import TimeoutError, as_completed
        from backend.infra.thread_pools import submit_rag_task
        import contextvars
        from backend.observability.tracer import trace_collector

        docs = []
        seen = set()
        # 按 query 分组检索，每个 doc 标记来源查询
        evidence_groups: dict[str, list] = {}
        retrieved_groups: dict[str, list] = {}
        query_results: dict[str, dict] = {}
        base_invoke = self.base_retriever.invoke
        fanout_span = trace_collector.start_span(
            "multi_query_fanout",
            name="多查询并发检索",
            type="retrieval",
            input={"queries": queries[:10]},
        )

        def _submit_isolated(q: str):
            # context 快照必须在提交方（请求）线程做：池线程 context 为空，
            # 在任务函数内 copy 会丢失全部请求状态（metadata_filter/主体授权/
            # 检索缓存），禁入库内容可经变体检索漏出（2026-09-14 冒烟实测）。
            # 每个任务持有独立 ctx 副本——多任务共享同一 ctx 并发 ctx.run 会抛
            # "cannot enter context: already entered"，变体检索全灭。
            #
            # Phase 3（2026-10-07）：父任务走 multi_query 专池——历史上提交进
            # retrieval_pool_inner，变体任务的子 leg（hybrid vector/BM25）排
            # 在同一池队列后方，2 个并发 MQ 请求即线程饥饿死锁。
            ctx = contextvars.copy_context()
            return submit_rag_task("multi_query", ctx.run, base_invoke, q)

        future_to_q = {}
        submitted_at = {}
        try:
            for q in queries:
                submitted_at[q] = time.perf_counter()
                future_to_q[_submit_isolated(q)] = q
        except Exception as e:
            message = str(e)[:300]
            cancelled_queries = set()
            for future, submitted_query in future_to_q.items():
                if future.cancel():
                    cancelled_queries.add(submitted_query)
            query_result_rows = []
            for q in queries:
                cancelled = q in cancelled_queries
                query_result_rows.append({
                    "query": q,
                    "status": "cancelled" if cancelled else "error",
                    "retrieved_docs": 0,
                    "retained_docs": 0,
                    "duration_ms": max(
                        0,
                        round((time.perf_counter() - submitted_at.get(q, time.perf_counter())) * 1000),
                    ),
                    "error": (
                        "任务提交失败，已取消等待"
                        if cancelled else f"检索任务提交失败：{message}"
                    ),
                })
            trace_collector.add_event(
                fanout_span,
                "fanout_submission_failed",
                "error",
                f"多查询检索任务提交失败：{message}",
                data={"error": message},
            )
            trace_collector.end_span(
                fanout_span,
                output={"query_results": query_result_rows, "partial": True},
                metrics={
                    "query_count": len(queries),
                    "success_count": 0,
                    "error_count": sum(item["status"] == "error" for item in query_result_rows),
                    "timeout_count": 0,
                    "unique_docs": 0,
                },
                status="error",
            )
            raise
        # Phase 5 §25：fan-out 限时——单个变体挂起不再无限等待；超时保留
        # 已完成变体的部分结果（部分成功语义），未完成的取消等待
        from backend.config.rag import RAG_MULTI_QUERY_FANOUT_TIMEOUT_MS
        from backend.rag.retrieval_budget import remaining_ms

        fanout_s = min(
            RAG_MULTI_QUERY_FANOUT_TIMEOUT_MS,
            remaining_ms(RAG_MULTI_QUERY_FANOUT_TIMEOUT_MS),
        ) / 1000
        fanout_timed_out = False
        try:
            for future in as_completed(future_to_q, timeout=fanout_s):
                q = future_to_q[future]
                try:
                    retrieved_docs = list(future.result() or [])
                    retrieved_groups[q] = retrieved_docs
                    evidence_groups[q] = []
                    query_results[q] = {
                        "query": q,
                        "status": "success",
                        "retrieved_docs": len(retrieved_docs),
                        "retained_docs": 0,
                        "duration_ms": max(
                            0, round((time.perf_counter() - submitted_at[q]) * 1000)
                        ),
                    }
                except Exception as e:
                    logger.warning(f"[MultiQuery] 检索失败: {e}")
                    retrieved_groups[q] = []
                    evidence_groups[q] = []
                    message = str(e)[:300]
                    query_results[q] = {
                        "query": q,
                        "status": "error",
                        "retrieved_docs": 0,
                        "retained_docs": 0,
                        "duration_ms": max(
                            0, round((time.perf_counter() - submitted_at[q]) * 1000)
                        ),
                        "error": message,
                    }
                    trace_collector.add_event(
                        fanout_span,
                        "query_failed",
                        "error",
                        f"查询「{q[:100]}」检索失败：{message}",
                        data={"query": q, "error": message},
                    )
        except TimeoutError:
            fanout_timed_out = True
            for f, q in future_to_q.items():
                if not f.done():
                    f.cancel()
                    message = "超过多查询检索时限，未完成的查询已取消等待"
                    query_results[q] = {
                        "query": q,
                        "status": "timeout",
                        "retrieved_docs": 0,
                        "retained_docs": 0,
                        "duration_ms": max(
                            0, round((time.perf_counter() - submitted_at[q]) * 1000)
                        ),
                        "error": message,
                    }
                    trace_collector.add_event(
                        fanout_span,
                        "query_timeout",
                        "error",
                        f"查询「{q[:100]}」超时",
                        data={"query": q, "error": message},
                    )
            logger.warning(
                f"[MultiQuery] fan-out 超时({fanout_s:.1f}s)，部分成功: "
                "已完成 "
                f"{sum(len(group) for group in retrieved_groups.values())} docs / "
                f"{len(queries)} 变体"
            )
        # 超时后未产出结果的变体补空组，保证 evidence_groups 键完整
        for q in queries:
            retrieved_groups.setdefault(q, [])
            evidence_groups.setdefault(q, [])
            query_results.setdefault(q, {
                "query": q,
                "status": "timeout" if fanout_timed_out else "error",
                "retrieved_docs": 0,
                "retained_docs": 0,
                "duration_ms": max(
                    0, round((time.perf_counter() - submitted_at[q]) * 1000)
                ),
                "error": "查询未返回可记录的结果",
            })

        # 按查询提交顺序稳定合并，原始 query 总是第一组。并发完成顺序不能
        # 决定重复 chunk 的归属或 rerank 输入顺序，否则相同请求会得到不同的
        # 候选顺序，且改写先完成时可能覆盖原始 query 的来源标记。
        for q in queries:
            q_docs = []
            for d in retrieved_groups[q]:
                cid = d.metadata.get("chunk_id", d.metadata.get("doc_id", "?"))
                if cid in seen:
                    continue
                seen.add(cid)
                d.metadata["source_query"] = q
                q_docs.append(d)
                docs.append(d)
            evidence_groups[q] = q_docs
            query_results[q]["retained_docs"] = len(q_docs)

        query_result_rows = [query_results[q] for q in queries]
        statuses = [item["status"] for item in query_result_rows]
        success_count = statuses.count("success")
        error_count = statuses.count("error")
        timeout_count = statuses.count("timeout")
        fanout_status = (
            "timeout" if timeout_count
            else "partial" if success_count and error_count
            else "error" if error_count
            else "success"
        )
        trace_collector.end_span(
            fanout_span,
            output={
                "query_results": query_result_rows,
                "unique_docs": len(docs),
                "partial": fanout_status != "success",
            },
            metrics={
                "query_count": len(queries),
                "success_count": success_count,
                "error_count": error_count,
                "timeout_count": timeout_count,
                "retrieved_docs": sum(
                    item["retrieved_docs"] for item in query_result_rows
                ),
                "unique_docs": len(docs),
            },
            status=fanout_status,
        )

        # 注入 evidence_groups 到首个 doc，供 prompt 模板使用
        if docs:
            docs[0].metadata["_evidence_groups"] = evidence_groups
            if fanout_timed_out:
                docs[0].metadata["_mq_partial"] = True  # Phase 5：部分成功留痕
            # Phase B（PR #52）：把本次实际提交且合法的查询列表交给最终
            # Rerank。只在真有改写（>1 条）时挂载——未触发、改写失败或只剩
            # 原始 query 时，Rerank 必须保持单查询行为，不得凭空放大评分。
            valid_queries = [
                q for q in queries if isinstance(q, str) and q.strip()
            ]
            if len(valid_queries) > 1:
                docs[0].metadata[RUNTIME_MQ_QUERIES_KEY] = valid_queries

        logger.info(
            f"[MultiQuery] {reason}: {query[:30]} → "
            f"{len(queries)}变体 → {len(docs)}docs"
            + ("（fan-out 部分成功）" if fanout_timed_out else "")
        )
        return docs
