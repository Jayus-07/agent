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
# sink 未挂载时的事件缓冲（2026-10-01 STOP C 请求隔离）：
# L2 历史裁剪发生在 MemoryManager 的后台 event loop 线程（5s 超时放弃后
# 协程仍会跑完），该线程没有 sink 也取不到请求 ContextVar —— 事件只能落
# 进程级缓冲。旧实现是单一 deque，任意 runner 一进来就整队取走——晚到
# 事件会冒充到别的用户连接上（事件归属缺陷）。现改为按会话分桶：
# runner 只 drain 本会话桶；无消费方的晚到事件按容量淘汰降级为日志/trace，
# 绝不流入下一位用户的连接。
_pending_events: dict[str, deque] = {}
_pending_lock = threading.Lock()
_PENDING_MAX_SESSIONS = 32      # 最多缓存的会话桶数（最旧桶整体淘汰）
_PENDING_PER_SESSION = 16       # 单会话桶容量（旧 deque maxlen=64 → 分桶后每桶 16）


def set_context_sink(sink: ContextSink) -> object:
    """挂载本轮请求的 context 事件 sink，返回 token 供 reset 使用。"""
    return _context_sink.set(sink)


def reset_context_sink(token: object) -> None:
    try:
        _context_sink.reset(token)  # type: ignore[arg-type]
    except Exception:
        _context_sink.set(None)


def drain_pending_events(session_id: str | None = None) -> list[dict]:
    """取走并清空【指定会话】缓冲的早期 context 事件（L2 等）。

    请求隔离（STOP C 2026-10-01）：只返回调用方会话自己的桶；不带
    session_id 的调用一律返回空——禁止任何 runner 整队取走他人事件。
    """
    key = (session_id or "").strip()
    if not key:
        logger.debug(
            "[ContextEvent] drain 未带会话 id，拒绝整队取走（请求隔离）")
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
    **extra: Any,
) -> None:
    """发一条 context SSE 事件（绝不携带完整工具结果）。

    sink 已挂载 → 直发；未挂载 → 按会话分桶缓冲（runner 按本会话 drain）。
    无 sink 且无会话归属的晚到事件只进日志——绝不冒充他人连接。
    格式（规格 §十一）：
      {"type": "context", "level": "L1", "action": "tool_compact",
       "before_tokens": 5200, "after_tokens": 250, "saved_tokens": 4950}
    """
    saved = max(0, before_tokens - after_tokens)
    data = {
        "type": "context",
        "level": level,
        "action": action,
        "before_tokens": before_tokens,
        "after_tokens": after_tokens,
        "saved_tokens": saved,
        "ts": time.time(),
        **extra,
    }
    sink = _context_sink.get()
    if sink is not None:
        try:
            sink(data)
            return
        except Exception:
            logger.debug("context SSE 事件发送失败", exc_info=True)
    key = (session_id or "").strip()
    if not key:
        logger.info(f"[ContextEvent] 无 sink 且无会话归属，降级日志: {data}")
        return
    try:
        with _pending_lock:
            if key not in _pending_events \
                    and len(_pending_events) >= _PENDING_MAX_SESSIONS:
                # FIFO 淘汰最旧的无消费方会话桶（晚到事件降级，不跨会话投递）
                oldest = next(iter(_pending_events))
                _pending_events.pop(oldest)
                logger.debug(
                    f"[ContextEvent] 会话事件桶超容量，淘汰最旧会话桶"
                    f"（count={_PENDING_MAX_SESSIONS}）")
            bucket = _pending_events.setdefault(key, deque(
                maxlen=_PENDING_PER_SESSION))
            bucket.append(data)
    except Exception:
        logger.debug("context 事件缓冲失败", exc_info=True)
