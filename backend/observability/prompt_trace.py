"""Prompt 版本追踪 — 将 prompt key@version 写入当前 trace 的 metadata。

在 PromptService.render / render_sync 完成后调用，
将版本信息追加到 trace.metadata["prompt_versions"] 列表。
无 active trace 时静默跳过。
"""
from __future__ import annotations

from backend.shared.logger import logger


def record_prompt_version(key: str, version: int | None, source: str) -> None:
    """将 prompt 版本信息写入当前 trace 的 metadata。

    线程安全：trace_collector.current() 基于 contextvars，
    写入 metadata 时 trace 仅在当前协程可见。
    """
    try:
        from backend.observability.tracer import trace_collector

        trace = trace_collector.current()
        if trace is None:
            return

        versions = trace.metadata.get("prompt_versions", [])
        entry = {"key": key, "version": version, "source": source}

        for existing in versions:
            if existing.get("key") == key:
                existing["version"] = version
                existing["source"] = source
                return

        versions.append(entry)
        trace.metadata["prompt_versions"] = versions
    except Exception:
        logger.debug("[prompt_trace] record_prompt_version failed", exc_info=True)
