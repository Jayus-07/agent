"""context_budget.tool_guard — L1 工具结果预算 Guard

拦截位置：Skill 执行边界（BaseSkill 两条执行路径 + SQLSkill/BusinessAnalysis
重写路径 + direct_executor），即"工具执行完成 → 写入 step_results"之间的
统一位置。不挂在 tools/map/_base.py::ok() —— 存量 18 个 Markdown Tool（例外
台账 E8）不经过它。

行为：
  - count_tokens(result) <= TOOL_INLINE_MAX_TOKENS → 原样返回
  - 超限 → 转 ToolResultRef 预览结构（按 token 截取 preview），并记录
    metric + 结构化日志 + SSE context 事件（L1/tool_compact）
"""

from __future__ import annotations

import json
from typing import Any

from backend.shared.logger import logger


def _enabled() -> bool:
    try:
        from backend.config import CONTEXT_BUDGET_ENABLED
        return bool(CONTEXT_BUDGET_ENABLED)
    except Exception:  # pragma: no cover — 配置缺失按开启处理
        return True


def _inline_max() -> int:
    from backend.config import TOOL_INLINE_MAX_TOKENS
    return TOOL_INLINE_MAX_TOKENS


def _preview_max() -> int:
    from backend.config import TOOL_PREVIEW_MAX_TOKENS
    return TOOL_PREVIEW_MAX_TOKENS


def serialize_for_count(output: Any) -> str:
    """把任意工具输出转成可计 token 的文本（str 原样，dict/list JSON 序列化）。"""
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    try:
        return json.dumps(output, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(output)


def truncate_text_to_tokens(text: str, max_tokens: int) -> str:
    """按 token 截取文本（非简单字符切片）。

    tiktoken 精确截取；编码器不可用时退化为字符数近似（2 字符/token，
    与 token_budget 降级口径一致），仅作极端 fallback。
    """
    if not text or max_tokens <= 0:
        return ""
    try:
        from backend.memory.token_budget import count_tokens
        if count_tokens(text) <= max_tokens:
            return text
    except Exception:  # pragma: no cover
        pass
    try:
        import tiktoken
        enc = tiktoken.get_encoding("o200k_base")
        return enc.decode(enc.encode(text)[:max_tokens])
    except Exception:
        # 极端 fallback：字符近似截取（仅编码器完全不可用时）
        return text[: max_tokens * 2]


def is_compacted_preview(output: Any) -> bool:
    """判断输出是否已是 L1 预览结构（L3 规则 3 依赖：不得重新拼回完整结果）。"""
    return (
        isinstance(output, dict)
        and output.get("context_compacted") is True
        and output.get("type") == "tool_result_preview"
    )


def guard_tool_result(
    output: Any,
    *,
    capability: str = "",
    step_id: str = "",
) -> Any:
    """L1 入口：工具结果写入 step_results 前的 token 预算检查。

    返回原输出（未超限）或预览 dict（超限）。所有 Skill/Tool 的结果
    （新 JSON Tool 与存量 Markdown Tool）都必须经过本 Guard。
    """
    if not _enabled() or output is None:
        return output

    text = serialize_for_count(output)
    from backend.memory.token_budget import count_tokens
    original_tokens = count_tokens(text)
    if original_tokens <= _inline_max():
        return output

    preview = truncate_text_to_tokens(text, _preview_max())
    inline_tokens = count_tokens(preview)

    from backend.context_budget.models import ToolResultRef
    ref = ToolResultRef(
        original_tokens=original_tokens,
        inline_tokens=inline_tokens,
        preview=preview,
        truncated=True,
    )

    # 观测：metric + 结构化日志 + SSE context 事件（level=L1）
    _record_compaction(
        level="L1", action="tool_compact", capability=capability,
        before_tokens=original_tokens, after_tokens=inline_tokens,
        step_id=step_id,
    )
    return ref.to_payload()


def _record_compaction(
    *, level: str, action: str, capability: str = "",
    before_tokens: int, after_tokens: int, step_id: str = "",
) -> None:
    """统一的压缩留痕：Prometheus + 结构化日志 + SSE context 事件。

    禁止 session_id/user_id/turn_id 进 Prometheus label（低基数约束）；
    step_id 只进日志，不进指标。
    """
    saved = max(0, before_tokens - after_tokens)
    try:
        from backend.context_budget.metrics import record_compaction
        record_compaction(level=level, action=action,
                          before_tokens=before_tokens, after_tokens=after_tokens)
    except Exception:
        logger.debug("context metric 记录失败", exc_info=True)

    logger.info(
        f"context_compacted level={level} action={action} "
        f"capability={capability or '-'} step_id={step_id or '-'} "
        f"before_tokens={before_tokens} after_tokens={after_tokens} saved_tokens={saved}"
    )
