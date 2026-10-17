"""context_budget.metrics — 上下文预算指标 + SSE context 事件出口

低基数约束（规格 §十四）：Prometheus label 只含 level/action/stage，
禁止 session_id/user_id/turn_id 进 label；这些 ID 只进结构化日志/Trace。

SSE context 事件采用与 infra.llm.proxy 的 stream_sink 相同的模式：
ContextVar 挂一个 sink callable，由 GraphRunner 每轮请求设置，Guard /
MicroCompactor 通过 emit_context_event 发声（sink 未挂载时静默跳过——
后台任务 / 测试路径无流式通道，属正常场景）。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable

from backend.shared.logger import logger

# ---------------------------------------------------------------------------
# Prometheus 指标（低基数）
# ---------------------------------------------------------------------------

# 指标定义统一收口在 observability/metrics.py（规格 §十四），此处仅引用；
# prometheus_client 不可用时软降级为 None（调用侧已判空）。
try:
    from backend.observability.metrics import (
        context_budget_overflow_total,
        context_autocompact_llm_tokens_total,
        context_compaction_latency_seconds,
        context_compactions_total,
        context_l5_total,
        context_protected_facts_total,
        context_tokens_saved_total,
    )
except Exception:  # pragma: no cover — observability 层不可用时软降级
    context_compactions_total = None
    context_tokens_saved_total = None
    context_budget_overflow_total = None
    context_compaction_latency_seconds = None
    context_autocompact_llm_tokens_total = None
    context_l5_total = None
    context_protected_facts_total = None


def record_compaction(
    *, level: str, action: str, before_tokens: int, after_tokens: int
) -> None:
    """记录一次压缩：metric + 结构化日志（不发声 SSE，见 emit_context_event）。"""
    saved = max(0, before_tokens - after_tokens)
    try:
        if context_compactions_total is not None:
            context_compactions_total.labels(level=level, action=action).inc()
        if context_tokens_saved_total is not None:
            context_tokens_saved_total.labels(level=level).inc(saved)
    except Exception:
        logger.debug("context metric 记录失败", exc_info=True)


def record_overflow(stage: str) -> None:
    """记录一次预算溢出（全量裁剪后仍超限）。"""
    try:
        if context_budget_overflow_total is not None:
            context_budget_overflow_total.labels(stage=stage).inc()
    except Exception:
        logger.debug("context metric 记录失败", exc_info=True)


def record_compaction_latency(*, level: str, seconds: float) -> None:
    """记录一次压缩耗时（L5 含 LLM 调用，秒级）。"""
    try:
        if context_compaction_latency_seconds is not None:
            context_compaction_latency_seconds.labels(level=level).observe(seconds)
    except Exception:
        logger.debug("context metric 记录失败", exc_info=True)


def record_summary_llm_tokens(*, prompt_tokens: int, completion_tokens: int) -> None:
    """记录 L5 摘要的 LLM token 消耗（成本观测）。"""
    try:
        if context_autocompact_llm_tokens_total is not None:
            context_autocompact_llm_tokens_total.labels(
                kind="prompt").inc(max(0, prompt_tokens))
            context_autocompact_llm_tokens_total.labels(
                kind="completion").inc(max(0, completion_tokens))
    except Exception:
        logger.debug("context metric 记录失败", exc_info=True)


# ── Phase 5 生产指标（低基数，§25/§26）─────────────────────────────

_L5_REASONS = frozenset({
    "success", "timeout", "provider_error", "empty_summary",
    "db_error", "stale_waterline", "disabled", "lock_conflict",
})
_FACT_TYPES = frozenset({
    "amount", "identifier", "percentage", "date", "url", "error_code", "other",
})
# 中文事实类型 → 低基数枚举（§26 固定集合）
_FACT_TYPE_MAP = {
    "金额": "amount",
    "订单号": "identifier",
    "SKU": "identifier",
    "业务ID": "identifier",
    "版本号": "identifier",
    "百分比": "percentage",
    "日期": "date",
    "时间": "date",
    "URL": "url",
    "错误码": "error_code",
}


def normalize_fact_type(fact_type: str) -> str:
    """中文事实类型 → Prometheus 低基数枚举；未知一律 other。"""
    return _FACT_TYPE_MAP.get(fact_type, "other")


def record_l5_attempt(*, status: str, reason: str) -> None:
    """记录一次 L5 尝试结果（status: success|failed|disabled）。

    reason 固定枚举（_L5_REASONS），非法值兜底 provider_error 防基数爆炸；
    禁止把 session_id/user_id/模型回复放 label。
    """
    if reason not in _L5_REASONS:
        reason = "provider_error"
    try:
        if context_l5_total is not None:
            context_l5_total.labels(status=status, reason=reason).inc()
    except Exception:
        logger.debug("context metric 记录失败", exc_info=True)


def record_protected_fact(*, fact_type: str, result: str) -> None:
    """记录 ProtectedFact 分层结果（result: extracted|preserved|patched）。"""
    t = normalize_fact_type(fact_type)
    if t not in _FACT_TYPES:  # 防御：normalize 已保证，双保险
        t = "other"
    try:
        if context_protected_facts_total is not None:
            context_protected_facts_total.labels(type=t, result=result).inc()
    except Exception:
        logger.debug("context metric 记录失败", exc_info=True)


def record_usage_components(**components: int) -> None:
    """分项 token 占用（STOP C 2026-10-01 口径）：

    - Gauge（context_tokens_by_component）：仅表示进程内最近一次写入值，
      并发下各分项甚至来自不同请求——禁止看板用它做聚合/解读单请求；
    - Histogram（context_tokens_by_component_tokens）：分项分布；
    - Counter（context_tokens_by_component_total）：分项累计总量（看板）；
    - 请求级分项 → 结构化日志（带 trace/request，本层无 ID 可用时缺省）。
    """
    try:
        from backend.observability.metrics import (
            context_tokens_by_component,
            context_tokens_by_component_tokens,
            context_tokens_by_component_total,
        )
        allowed = ("system", "history", "previous_outputs", "rag",
                   "tool_schema")
        for name, value in components.items():
            if name not in allowed:
                continue
            v = max(0, int(value))
            if context_tokens_by_component is not None:
                context_tokens_by_component.labels(component=name).set(v)
            if context_tokens_by_component_tokens is not None:
                context_tokens_by_component_tokens.labels(
                    component=name).observe(v)
            if context_tokens_by_component_total is not None:
                context_tokens_by_component_total.labels(
                    component=name).inc(v)
        logger.info(
            "context_usage_components "
            + " ".join(f"{k}={max(0, int(v))}"
                       for k, v in sorted(components.items())
                       if k in allowed)
            + " trace_id=%s request_id=%s",
            _trace_id_or_empty(), _request_id_or_empty())
    except Exception:
        logger.debug("component gauge 记录失败", exc_info=True)


def _trace_id_or_empty() -> str:
    try:
        from backend.infra.llm.proxy import get_current_resolved_model
        ctx = get_current_resolved_model()
        return (ctx.trace_id if ctx else "") or ""
    except Exception:
        return ""


def _request_id_or_empty() -> str:
    try:
        from backend.infra.llm.proxy import get_current_resolved_model
        ctx = get_current_resolved_model()
        return (ctx.request_id if ctx else "") or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# SSE context 事件出口（ContextVar sink，模式与 stream_sink 一致）
# ---------------------------------------------------------------------------

# data 形如 {"type": "context", "level": "L1", "action": "tool_compact", ...}
ContextSink = Callable[[dict], None]
_context_sink: ContextVar[ContextSink | None] = ContextVar(
    "context_event_sink", default=None
)
_context_sink_scope: ContextVar["ContextEventScope | None"] = ContextVar(
    "context_event_sink_scope", default=None
)


@dataclass(frozen=True)
class ContextEventScope:
    """内部 SSE 事件归属；所有字段都必须来自可信请求上下文。"""

    product_id: str
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str

    @property
    def complete(self) -> bool:
        return all((
            self.product_id, self.tenant_id, self.user_id,
            self.session_id, self.request_id,
        ))


_current_event_scope: ContextVar[ContextEventScope | None] = ContextVar(
    "context_event_scope", default=None
)

# 仅允许无原文的固定结构元数据进入 SSE；fold_id 只接受内部 UUID 摘要。
_SAFE_CONTEXT_EVENT_EXTRA = frozenset({
    "reversible", "fold_id", "folded_messages", "replaced_messages",
    "summary_tokens", "delta_messages",
})
_CONTEXT_EVENT_LEVELS = frozenset({"L1", "L2", "L3", "L4", "L5"})
_CONTEXT_EVENT_ACTIONS = frozenset({
    "collapse", "deferred", "auto_compact", "history_trim",
    "previous_outputs_compact",
})

# 缓冲区只承接本轮会话加载阶段已完成、且明确归属当前请求的事件。
# request_id 是作用域的一部分；旧请求迟到事件永远不能被下一请求排空。
_pending_events: dict[ContextEventScope, deque] = {}
_pending_lock = threading.Lock()
_PENDING_MAX_SCOPES = 32        # 最多缓存的请求作用域桶数（最旧桶整体淘汰）
_PENDING_PER_SCOPE = 16         # 单请求桶容量


def set_context_event_scope(scope: ContextEventScope | None) -> object:
    """绑定本次请求的事件归属；缺少完整身份时保持 fail-closed。"""
    return _current_event_scope.set(scope if scope and scope.complete else None)


def reset_context_event_scope(token: object) -> None:
    try:
        _current_event_scope.reset(token)  # type: ignore[arg-type]
    except Exception:
        _current_event_scope.set(None)


def set_context_sink(
    sink: ContextSink, *, scope: ContextEventScope | None = None,
) -> tuple[object, object]:
    """挂载请求级事件 sink 和归属，返回 token 供 reset 使用。"""
    active_scope = scope or _current_event_scope.get()
    return (
        _context_sink.set(sink),
        _context_sink_scope.set(
            active_scope if active_scope and active_scope.complete else None
        ),
    )


def reset_context_sink(token: object) -> None:
    try:
        sink_token, scope_token = token  # type: ignore[misc]
        _context_sink.reset(sink_token)  # type: ignore[arg-type]
        _context_sink_scope.reset(scope_token)  # type: ignore[arg-type]
    except Exception:
        _context_sink.set(None)
        _context_sink_scope.set(None)


def drain_pending_events(
    *, scope: ContextEventScope | None = None,
) -> list[dict]:
    """取走当前请求作用域的早期事件；不再支持按会话名排空。

    作用域缺少产品、租户、用户、会话或请求任一字段时返回空。特别是
    request_id 不匹配时，晚到的旧请求事件留在原桶并最终按容量淘汰。
    """
    key = scope or _current_event_scope.get()
    if key is None or not key.complete:
        logger.debug("[ContextEvent] 缺少完整请求作用域，拒绝排空事件")
        return []
    with _pending_lock:
        bucket = _pending_events.pop(key, None)
    return list(bucket) if bucket else []


def emit_context_event(
    *,
    level: str,
    action: str,
    before_tokens: int,
    after_tokens: int,
    session_id: str | None = None,
    event_scope: ContextEventScope | None = None,
    **extra: Any,
) -> None:
    """发一条 context SSE 事件（绝不携带完整工具结果）。

    sink 已挂载时，仅当事件作用域与 sink 作用域完全相同才发送。未挂载时，
    只有带完整 Product/Tenant/User/Session/Request 作用域的事件才进入缓冲；
    缺少任一可信标识时只保留安全日志，不进入后续请求。
    格式（规格 §十一）：
      {"type": "context", "level": "L3", "action": "previous_outputs_compact",
       "before_tokens": 5200, "after_tokens": 250, "saved_tokens": 4950}
    """
    if not isinstance(level, str) or level not in _CONTEXT_EVENT_LEVELS \
            or not isinstance(action, str) \
            or action not in _CONTEXT_EVENT_ACTIONS \
            or type(before_tokens) is not int or before_tokens < 0 \
            or type(after_tokens) is not int or after_tokens < 0:
        logger.debug("[ContextEvent] 非法事件元数据，丢弃")
        return
    saved = max(0, before_tokens - after_tokens)
    scope = event_scope or _current_event_scope.get()
    safe_extra = _safe_event_extra(extra)
    data = {
        "type": "context",
        "level": level,
        "action": action,
        "before_tokens": before_tokens,
        "after_tokens": after_tokens,
        "saved_tokens": saved,
        "ts": time.time(),
        **safe_extra,
    }
    sink = _context_sink.get()
    if sink is not None:
        sink_scope = _context_sink_scope.get()
        if scope is None or not scope.complete or scope != sink_scope:
            logger.debug(
                "[ContextEvent] SSE 事件归属与当前请求不匹配，丢弃")
            return
        try:
            sink(data)
            return
        except Exception:
            logger.debug("context SSE 事件发送失败", exc_info=True)
    if scope is None or not scope.complete:
        logger.info(
            "[ContextEvent] 无完整请求作用域，事件不进入缓冲: "
            "level=%s action=%s before_tokens=%s after_tokens=%s",
            level, action, before_tokens, after_tokens,
        )
        return
    try:
        with _pending_lock:
            if scope not in _pending_events \
                    and len(_pending_events) >= _PENDING_MAX_SCOPES:
                # FIFO 淘汰最旧的无消费方请求桶；不跨请求投递。
                oldest = next(iter(_pending_events))
                _pending_events.pop(oldest)
                logger.debug(
                    "[ContextEvent] 请求事件桶超容量，淘汰最旧请求桶"
                    "（count=%s）", _PENDING_MAX_SCOPES,
                )
            bucket = _pending_events.setdefault(scope, deque(
                maxlen=_PENDING_PER_SCOPE))
            bucket.append(data)
    except Exception:
        logger.debug("context 事件缓冲失败", exc_info=True)


def _safe_event_extra(extra: dict[str, Any]) -> dict[str, Any]:
    """只放行约定的结构元数据，拒绝原文、任意对象及未识别字段。"""
    safe: dict[str, Any] = {}
    for name, value in extra.items():
        if name not in _SAFE_CONTEXT_EVENT_EXTRA:
            continue
        if name == "reversible" and isinstance(value, bool):
            safe[name] = value
        elif name == "fold_id" and isinstance(value, str) \
                and len(value) == 17 and value.startswith("fold-") \
                and all(char in "0123456789abcdef" for char in value[5:]):
            safe[name] = value
        elif name != "reversible" and name != "fold_id" \
                and isinstance(value, int) and not isinstance(value, bool) \
                and value >= 0:
            safe[name] = value
    return safe
