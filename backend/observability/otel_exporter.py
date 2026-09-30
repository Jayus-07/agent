"""observability/otel_exporter.py — 自研 trace 的 OTel 第二出口（P2-3）

设计（2026-09-30 主架构改造，用户已拍板）：
  - **不替换现有自研 trace**（trace_collector → PG 存储保持权威），
    只是给每个收口的 span 镜像一份到 OTLP collector
  - 接线用 trace_collector 现成的 subscribe listener 机制——end_span 尾部
    回调，tracer.py 零改动
  - 导出走 opentelemetry-sdk 的 BatchSpanProcessor（内置有界队列 + 后台
    线程批量导出）：listener 回调只做内存操作构造镜像 span，网络 IO 全在
    后台线程，不阻塞 span 收口路径
  - fire-and-forget：导出失败仅 debug（OTel 库自身日志压到 CRITICAL 防洪泛）
  - 双条件启用：OTEL_TRACE_OTLP_ENABLED（默认 false）+
    OTEL_EXPORTER_OTLP_ENDPOINT 已配置
  - 零新增依赖：sdk / proto-http exporter 已在 requirements-lock（1.41.1）
"""
from __future__ import annotations

import logging
from typing import Any

from backend.config import OTEL_EXPORTER_OTLP_ENDPOINT, OTEL_TRACE_OTLP_ENABLED
from backend.shared.logger import logger

# 镜像 span 的 input/output 摘要截断（属性值上限，防大 payload 打爆 collector）
_ATTR_VALUE_MAX = 512


def _clip(value: Any) -> str:
    text = str(value)
    return text if len(text) <= _ATTR_VALUE_MAX else text[:_ATTR_VALUE_MAX] + "…"


def _attr_value(value: Any) -> Any:
    """数值保持原类型（collector 侧可聚合），其余转截断字符串。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    return _clip(value)


class OtelSpanMirror:
    """把自研 Span 镜像为 OTel span 交 BatchSpanProcessor 后台导出。"""

    def __init__(self, endpoint: str, exporter: Any = None):
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        self._provider = TracerProvider()
        if exporter is None:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            exporter = OTLPSpanExporter(endpoint=endpoint)
        self._exporter = exporter
        self._provider.add_span_processor(BatchSpanProcessor(exporter))
        self._tracer = self._provider.get_tracer("agent-platform.trace-mirror")
        # 导出失败仅 debug：压掉 OTel 库自身的重试/失败日志洪泛
        logging.getLogger("opentelemetry").setLevel(logging.CRITICAL)

    def emit(self, trace, span) -> None:
        """listener 回调（end_span 尾部同步调用，必须只做内存操作）。"""
        from opentelemetry.trace import Status, StatusCode

        otel_span = self._tracer.start_span(span.name or span.span_id)
        otel_span.set_attribute("agent.trace_id", getattr(trace, "id", "") or "")
        otel_span.set_attribute("agent.span_id", span.span_id)
        if span.parent_id:
            otel_span.set_attribute("agent.parent_id", span.parent_id)
        otel_span.set_attribute("agent.span_kind", str(getattr(span, "kind", "")))
        otel_span.set_attribute("agent.span_type", str(getattr(span, "type", "")))
        otel_span.set_attribute("agent.status", span.status)
        otel_span.set_attribute("agent.duration_ms", span.duration_ms)
        if span.retry_count:
            otel_span.set_attribute("agent.retry_count", span.retry_count)
        for key, value in (span.metrics or {}).items():
            otel_span.set_attribute(f"agent.metrics.{key}", _attr_value(value))
        if span.input:
            otel_span.set_attribute("agent.input", _clip(span.input))
        if span.output:
            otel_span.set_attribute("agent.output", _clip(span.output))
        if span.status == "error":
            otel_span.set_status(Status(StatusCode.ERROR))
            if span.errors:
                otel_span.set_attribute("agent.errors", _clip(span.errors))
        otel_span.end()

    def shutdown(self) -> None:
        """进程退出时冲刷队列（尽力而为，超时由 SDK 默认值兜底）。"""
        try:
            self._provider.shutdown()
        except Exception:  # noqa: BLE001
            logger.debug("[OtelMirror] shutdown 失败（忽略）", exc_info=True)


_mirror: OtelSpanMirror | None = None
_installed = False


def install_if_enabled() -> bool:
    """按配置安装镜像 listener（幂等）。返回是否已启用。

    开关关 / endpoint 未配置 / OTel 包不可用 → no-op（首次失败 debug 一次）。
    """
    global _mirror, _installed
    if _installed:
        return _mirror is not None
    _installed = True

    if not OTEL_TRACE_OTLP_ENABLED:
        return False
    if not OTEL_EXPORTER_OTLP_ENDPOINT:
        logger.debug("[OtelMirror] OTEL_TRACE_OTLP_ENABLED 已开但未配置 OTEL_EXPORTER_OTLP_ENDPOINT，跳过")
        return False
    try:
        from backend.observability.tracer import trace_collector

        _mirror = OtelSpanMirror(OTEL_EXPORTER_OTLP_ENDPOINT)

        def _on_span_end(trace, span) -> None:
            # listener 回调软失败：镜像挂了不能影响自研 trace 主链路
            try:
                _mirror.emit(trace, span)
            except Exception:  # noqa: BLE001
                logger.debug("[OtelMirror] span 镜像失败（软失败）", exc_info=True)

        trace_collector.subscribe(_on_span_end)
        logger.info(
            "[OtelMirror] OTel 镜像导出已启用 → %s（自研 trace/PG 存储不变）",
            OTEL_EXPORTER_OTLP_ENDPOINT,
        )
        return True
    except Exception as e:  # noqa: BLE001 — 初始化失败软降级
        logger.warning(f"[OtelMirror] 初始化失败，OTel 导出不可用（其余功能不受影响）: {e}")
        _mirror = None
        return False
