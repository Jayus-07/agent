"""Trace PII 脱敏 + detail_level 控制。

在 API 边界（DTO 层）对 trace 数据进行脱敏处理，内部存储保持完整保真。
支持三级 detail_level:
  - full: 原始数据，不做任何处理
  - masked: PII 脱敏（手机号/身份证/邮箱等替换为类型标签）
  - summary: 脱敏 + 剥离 span body（input/output/prompt/response）
"""
from __future__ import annotations

from typing import Any

from backend.config.observability import (
    TRACE_PII_MASKING_ENABLED,
    TRACE_DETAIL_LEVEL,
)
from backend.shared.logger import logger


def mask_text(text: str) -> str:
    """对文本执行 PII 脱敏。委托给 pii_filter.scan_and_sanitize。"""
    if not text or not isinstance(text, str):
        return text or ""
    if not TRACE_PII_MASKING_ENABLED:
        return text
    try:
        from backend.memory.pii_filter import scan_and_sanitize
        result = scan_and_sanitize(text)
        return result.sanitized
    except Exception:
        logger.debug("[Redaction] PII mask failed, returning original", exc_info=True)
        return text


def _mask_dict(d: Any) -> Any:
    """递归对 dict 中的 str 值执行 PII 脱敏。"""
    if isinstance(d, str):
        return mask_text(d)
    if isinstance(d, dict):
        return {k: _mask_dict(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_mask_dict(item) for item in d]
    return d


def redact_trace_dto(dto: dict, level: str | None = None) -> dict:
    """根据 detail_level 对 trace DTO 做脱敏处理。

    Args:
        dto: to_trace_dto / stored_dict_to_dto 产出的 dict。
        level: "full" / "masked" / "summary"；None 时读取配置默认值。

    Returns:
        新的 dict（不修改原始对象）。
    """
    level = level or TRACE_DETAIL_LEVEL
    if level == "full":
        return dto

    import copy
    redacted = copy.deepcopy(dto)

    if level in ("masked", "summary"):
        redacted["question"] = mask_text(redacted.get("question", ""))
        redacted["answer_preview"] = mask_text(redacted.get("answer_preview", ""))
        redacted["metadata"] = _mask_dict(redacted.get("metadata", {}))
        redacted["tags"] = _mask_dict(redacted.get("tags", {}))

        for span in redacted.get("spans", []):
            span["input"] = _mask_dict(span.get("input"))
            span["output"] = _mask_dict(span.get("output"))
            span["events"] = _mask_dict(span.get("events", []))
            span["errors"] = _mask_dict(span.get("errors", []))
            if "llm_call" in span:
                lc = span["llm_call"]
                lc["prompt_text"] = mask_text(lc.get("prompt_text", ""))
                lc["response_text"] = mask_text(lc.get("response_text", ""))

    if level == "summary":
        for span in redacted.get("spans", []):
            span["input"] = None
            span["output"] = None
            span["events"] = []
            if "llm_call" in span:
                span["llm_call"]["prompt_text"] = ""
                span["llm_call"]["response_text"] = ""

    return redacted


def redact_stored_dict(d: dict, level: str | None = None) -> dict:
    """对 SQLite stored dict 做脱敏（用于 stored_dict_to_dto 之前的原始数据）。"""
    return redact_trace_dto(d, level)
