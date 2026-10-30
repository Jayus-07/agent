"""统一 llm_call span 出口 — proxy 层每次 LLM 调用的可观测埋点（2026-10-30）。

根因：llm_call span 此前只有调用方散点埋点（rag/chain.py llm_generate、
tool_selector、sql_agent 阶段 span、self_correction_rewrite、reranker），
主图 general_chat 等直连 backend/infra/llm 的调用没有任何 span，导致
workflow_name=agent 的 trace llm_call span 恒为 0，而 llm_usage 计费明细
正常——两条采集链未打通。

出口选型：所有成功调用（invoke/ainvoke/batch/generate/stream/astream/
record_llm_result）都汇聚到 proxy._record_tokens / _settle_estimated_stream_usage，
在本模块创建并立即收口 span（调用结束时回写真实时间窗），替代后续新调用点的散点埋点。

与 llm_usage_store 的关联口径（不双计契约）：
- 一个成功 LLM 调用 = 恰一行 llm_usage（_record_tokens 落库，带 trace_id）
  + 恰一个 llm_call span（本模块创建）；span 指标与 usage 行同源同值。
- trace.usage 主口径 = finish() 时 _backfill_usage_from_store 按 llm_usage
  明细聚合覆盖 _aggregate_usage 的 span 求和——两级结果一致，不叠加。
- 调用方已管理 LLM span（最内层未收口 span 的 kind=llm 或 type=llm_call）
  时跳过创建，同一调用不产生第二个 span（也避免 backfill 最近邻配对被占行）。

脱敏与文本约束：
- prompt_text/response_text 只写入截断快照（各 500 字），与 rag/chain.py
  llm_generate 现行口径一致；masked/summary 级由 redact_trace_dto 在 DTO 层
  打码/剥离（复用 backend.observability.redaction，此处不做二次脱敏）。
"""
from __future__ import annotations

import time
from contextvars import ContextVar

from backend.shared.logger import logger

# 截断上限：与 chain.py llm_generate 的文本截断同级（1000 内取更保守的 500）
_SNAPSHOT_MAX_CHARS = 500

# 调用前暂存的 prompt 快照（ContextVar 隔离并发请求；流式与韧性链入口写入，
# span 出口消费并复位，避免串到下一次调用）
_prompt_snapshot_var: ContextVar[str] = ContextVar(
    "llm_call_prompt_snapshot", default="",
)
# 流式路径逐 chunk 累积的 response 快照（非流式路径从 result 提取）
_response_snapshot_var: ContextVar[str] = ContextVar(
    "llm_call_response_snapshot", default="",
)


def note_prompt_snapshot(args: tuple) -> None:
    """从 LLM 调用入参提取截断 prompt 快照，供本调用的 llm_call span 使用。

    兼容形态：str、PromptValue（.content/.to_string）、
    ("human", content) 消息元组、BaseMessage 列表、OpenAI dict.messages。
    同时复位上一次调用的流式 response 快照——本函数在每次调用入口执行，
    是唯一可靠的快照周期边界。任何失败静默留空，不能影响主链路。
    """
    _response_snapshot_var.set("")
    try:
        payload = args[0] if args else None
        text = ""
        if isinstance(payload, str):
            text = payload
        elif isinstance(payload, (list, tuple)) and payload:
            last = payload[-1]
            if isinstance(last, tuple) and len(last) >= 2:
                text = str(last[1] or "")
            else:
                text = str(getattr(last, "content", last) or "")
        elif payload is not None:
            text = str(getattr(payload, "content", payload) or "")
        _prompt_snapshot_var.set(text[:_SNAPSHOT_MAX_CHARS])
    except Exception:
        _prompt_snapshot_var.set("")


def note_response_chunk(text: str) -> None:
    """流式路径逐 chunk 累积 response 快照（达到上限后不再追加）。"""
    if not text:
        return
    try:
        buf = _response_snapshot_var.get()
    except Exception:
        return
    if len(buf) >= _SNAPSHOT_MAX_CHARS:
        return
    _response_snapshot_var.set((buf + text)[:_SNAPSHOT_MAX_CHARS])


def consume_prompt_snapshot() -> str:
    """读取并复位 prompt 快照（保证一次调用只归属一次 span）。"""
    try:
        text = _prompt_snapshot_var.get()
    except Exception:
        return ""
    _prompt_snapshot_var.set("")
    return text or ""


def consume_response_snapshot() -> str:
    """读取并复位流式 response 快照。"""
    try:
        text = _response_snapshot_var.get()
    except Exception:
        return ""
    _response_snapshot_var.set("")
    return text or ""


def record_llm_call_span(
    *,
    metrics: dict,
    result=None,
    duration_ms: float = 0.0,
) -> None:
    """为一次已结算的 LLM 调用创建并收口 llm_call span（软失败，绝不抛错）。

    Args:
        metrics: _last_call_meta_var 快照（token/成本/模型身份同源字段）。
        result: provider 返回值（提取 response 文本快照）。
        duration_ms: 调用方计得的真实耗时（含重试/熔断等待）。
    """
    try:
        from backend.observability.tracer import SpanKind, trace_collector

        # 快照在一切早退前消费并复位（dedup/无 trace 路径同样清理，
        # 防止串到下一次调用）
        prompt_text = consume_prompt_snapshot()
        stream_response = consume_response_snapshot()

        trace = trace_collector.current()
        if trace is None:
            return
        # 双计防线：最内层未收口 span 已是调用方管理的 LLM span 时跳过
        open_spans = getattr(trace, "_open_spans", None) or []
        if open_spans:
            innermost = open_spans[-1]
            if getattr(innermost, "kind", "") == SpanKind.LLM.value \
                    or getattr(innermost, "type", "") == "llm_call":
                return

        model = str(metrics.get("model") or metrics.get("model_name") or "")
        response_text = stream_response or _extract_response_text(result)

        span = trace_collector.start_span(
            "llm_call_proxy", name="LLM 调用",
            type="llm_call", kind=SpanKind.LLM.value,
            input={"prompt": prompt_text} if prompt_text else None,
        )
        if getattr(span, "_noop", False):
            return
        span_metrics = {
            "model_name": model,
            "provider": str(metrics.get("provider") or ""),
            "prompt_tokens": int(metrics.get("prompt_tokens") or 0),
            "completion_tokens": int(metrics.get("completion_tokens") or 0),
            "total_tokens": int(metrics.get("total_tokens") or 0),
            "cached_tokens": int(metrics.get("cached_tokens") or 0),
            "reasoning_tokens": int(metrics.get("reasoning_tokens") or 0),
            # 记账本位币主口径（Billing V2）：cost_cny 权威；cost_usd 兼容
            "cost_cny": float(metrics.get("cost_cny") or 0.0),
            "cost_usd": float(metrics.get("cost_usd") or 0.0),
            "duration_ms_call": round(float(duration_ms or 0.0), 1),
            "finish_reason": str(metrics.get("finish_reason") or ""),
            "model_role": str(metrics.get("model_role") or ""),
            "binding_source": str(metrics.get("binding_source") or ""),
            "usage_source": str(metrics.get("usage_source") or ""),
            # 与 backfill（token_source=llm_usage_backfill）区分：
            # 本 span 指标与 llm_usage 行同源，回填配对不再占用该行
            "token_source": "proxy_span",
        }
        output = {"response": response_text} if response_text else None
        trace_collector.end_span(span, metrics=span_metrics, output=output)
        # span 在调用结束后创建，_t0 计得的 duration≈0；用真实调用耗时
        # 回写时间窗，瀑布图按真实调用区间呈现（start=end-duration）。
        window_ms = max(1.0, float(duration_ms or 0.0))
        span.duration_ms = max(1, round(window_ms))
        span.end_time = span.end_time or _now_iso()
        span.start_time = _iso_offset(span.end_time, window_ms / 1000.0)
    except Exception:
        logger.debug("[llm_call_span] 统一埋点失败（忽略）", exc_info=True)


def _extract_response_text(result) -> str:
    """从 provider 返回值提取截断 response 快照。"""
    try:
        content = getattr(result, "content", result)
        if isinstance(content, str):
            return content[:_SNAPSHOT_MAX_CHARS]
        if content:
            return str(content)[:_SNAPSHOT_MAX_CHARS]
    except Exception:
        pass
    return ""


def _now_iso() -> str:
    from backend.observability.tracer import _now_iso as _tracer_now

    return _tracer_now()


def _iso_offset(end_iso: str, offset_sec: float) -> str:
    """按 end 时间回推 offset 秒，产出与 tracer 同格式的 UTC ISO 毫秒串。"""
    from datetime import datetime, timezone

    try:
        dt = datetime.fromisoformat((end_iso or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        start = dt.timestamp() - max(0.0, float(offset_sec))
        s = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(start))
        return f"{s}.{int((start % 1) * 1000):03d}Z"
    except Exception:
        return end_iso


__all__ = [
    "note_prompt_snapshot",
    "note_response_chunk",
    "consume_prompt_snapshot",
    "consume_response_snapshot",
    "record_llm_call_span",
]
