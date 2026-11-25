"""
backend/infra/llm/gateway/streaming.py — 流式 Sink 管理与分片解析
"""
from __future__ import annotations

import contextvars as _contextvars
from typing import Any

from backend.infra.llm.resolved_model import get_current_resolved_model
from backend.shared.logger import logger

_stream_sink_var: _contextvars.ContextVar = _contextvars.ContextVar(
    "llm_stream_sink", default=None,
)


def set_stream_sink(sink) -> None:
    """设置当前上下文的流式增量回调 sink(text: str) -> None。"""
    _stream_sink_var.set(sink)


def reset_stream_sink() -> None:
    """清除当前上下文的流式 sink（请求结束时调用，防线程复用串味）。"""
    _stream_sink_var.set(None)


def emit_stream_delta(text: str, kind: str = "answer") -> bool:
    """把生成增量转发给当前 sink。无 sink / 空 text / sink 失败均静默跳过。

    Args:
        text: 增量文本
        kind: "answer"（默认，正文 delta）或 "thinking"（推理模型思考链增量）
    Returns:
        是否真正转发了（供调用方判断本轮是否发生过流式输出）。
    """
    if not text:
        return False
    sink = _stream_sink_var.get()
    if sink is None:
        return False
    try:
        try:
            sink(text, kind)
        except TypeError:
            # 兼容旧式单参 sink(text)（测试桩/未升级的调用方）
            sink(text)
        return True
    except Exception:
        return False


def extract_chunk_text(chunk: Any) -> str:
    """从流式 chunk 提取文本（兼容 str / AIMessageChunk / 多模态 content list）。"""
    if isinstance(chunk, str):
        return chunk
    c = getattr(chunk, "content", "")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(
            p.get("text", "") for p in c if isinstance(p, dict)
        )
    return str(c) if c else ""


def extract_chunk_reasoning(chunk: Any) -> str:
    """从流式 chunk 提取思考链增量（推理模型 reasoning_content）。

    LangChain 不解析 reasoning_content，思考型模型（deepseek-reasoner /
    qwen3 thinking）流式期间它挂在 additional_kwargs 上且 content 为空。
    非思考模型/str chunk 返回空串，调用方零成本跳过。
    """
    if isinstance(chunk, str):
        return ""
    kw = getattr(chunk, "additional_kwargs", None) or {}
    reasoning = kw.get("reasoning_content", "")
    return reasoning if isinstance(reasoning, str) else ""


def _notify_stream_usage_missing(mode: str) -> None:
    """流式调用未取得 usage chunk 的显式观测（C13）：计数 + 一次性日志。

    覆盖 provider 不回 stream usage（ChatAnthropic/ChatOllama 无
    stream_options.include_usage）与客户端提前中断两类场景 —— 禁止沉默。
    """
    try:
        from backend.observability.metrics import llm_usage_missing_total
        llm_usage_missing_total.inc()
    except Exception:
        pass
    try:
        model = get_current_resolved_model().model_id \
            if get_current_resolved_model() is not None else ""
        logger.warning(
            "[LLM:proxy] %s 流式结束但未取得 usage chunk（provider 未回传或客户端提前中断），"
            "本次 token 用量缺失 model=%s", mode, model or "unknown",
        )
    except Exception:
        pass


def _record_stream_attempt(
    attempt: int,
    duration_ms: float,
    first_chunk_ms: float | None,
    *,
    error_type: str = "",
) -> None:
    """流式单次尝试的观测留痕（2026-10-29 供应商冷启动排障）。"""
    model = ""
    try:
        resolved = get_current_resolved_model()
        model = resolved.model_id if resolved is not None else ""
    except Exception:
        pass
    logger.info(
        "[LLM:stream] attempt=%d model=%s duration_ms=%.0f first_chunk_ms=%s error=%s",
        attempt, model or "unknown", duration_ms,
        "n/a" if first_chunk_ms is None else f"{first_chunk_ms:.0f}",
        error_type or "none",
    )
    try:
        from backend.observability.tracer import trace_collector

        t = trace_collector.current()
        span = t.spans[-1] if t is not None and getattr(t, "spans", None) else None
        if span is not None:
            trace_collector.add_event(
                span, "llm_stream_attempt", "info",
                f"流式尝试 {attempt} 结束（{error_type or '成功'}）",
                data={
                    "attempt": attempt,
                    "model": model,
                    "duration_ms": round(duration_ms, 1),
                    "first_chunk_ms": (
                        None if first_chunk_ms is None else round(first_chunk_ms, 1)
                    ),
                    "error_type": error_type,
                },
            )
    except Exception:
        pass
