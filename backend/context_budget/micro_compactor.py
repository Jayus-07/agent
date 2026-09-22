"""context_budget.micro_compactor — L3 previous_outputs 微压缩

作用对象是 step_results / previous_outputs（工具输出驻留地），不是聊天历史。

规则（基础版）：
  1. 总预算：全部 previous_outputs 不超过 PREVIOUS_OUTPUTS_MAX_TOKENS
  2. 最新结果优先：超预算时优先完整保留最新条目，旧条目降级为
     {step_id, tool, status, preview, compacted: True}——不无声丢失
  3. L1 已产生 preview 的条目不允许重新获取或拼回完整结果（只允许
     preview 再收缩，compacted 标记保留）
"""

from __future__ import annotations

from typing import Any

from backend.context_budget.tool_guard import (
    _record_compaction,
    is_compacted_preview,
    serialize_for_count,
    truncate_text_to_tokens,
)

# 降级条目的 preview 上限（token）：比 L1 的 TOOL_PREVIEW_MAX_TOKENS 更紧，
# 旧结果只需要"知道有什么"而不需要完整内容
_PREVIEW_DEGRADE_TOKENS = 128


def _enabled() -> bool:
    try:
        from backend.config import CONTEXT_BUDGET_ENABLED
        return bool(CONTEXT_BUDGET_ENABLED)
    except Exception:  # pragma: no cover
        return True


def _budget() -> int:
    from backend.config import PREVIOUS_OUTPUTS_MAX_TOKENS
    return PREVIOUS_OUTPUTS_MAX_TOKENS


def _count_output_tokens(output: Any) -> int:
    from backend.memory.token_budget import count_tokens
    return count_tokens(serialize_for_count(output))


def _degrade_entry(
    dep_id: str,
    output: Any,
    meta: dict | None,
    *,
    preview_tokens: int = _PREVIEW_DEGRADE_TOKENS,
) -> dict:
    """把一条输出降级为摘要结构（保留可追溯的最小信息，不无声丢失）。"""
    from backend.memory.token_budget import count_tokens

    already_compacted = is_compacted_preview(output)
    if already_compacted:
        # L1 preview：不拼回完整结果，只允许 preview 再收缩
        preview = truncate_text_to_tokens(
            str(output.get("preview", "")), preview_tokens)
        original_tokens = int(output.get("original_tokens", 0)) or None
    else:
        text = serialize_for_count(output)
        preview = truncate_text_to_tokens(text, preview_tokens)
        original_tokens = count_tokens(text)

    entry: dict[str, Any] = {
        "step_id": (meta or {}).get("step_id", dep_id),
        "tool": (meta or {}).get("tool", ""),
        "status": (meta or {}).get("status", "success"),
        "preview": preview,
        "compacted": True,
    }
    if original_tokens:
        entry["original_tokens"] = original_tokens
    return entry


def compact_previous_outputs(
    previous_outputs: dict[str, Any] | None,
    *,
    meta: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """L3 入口：对注入下一 Skill prompt 的 previous_outputs 做总预算压缩。

    meta: 可选的 {dep_id: {step_id, tool, status}} 元数据表（调用方从
    step_results 提取），降级条目用它保留可追溯信息；缺省以 dep_id 兜底。

    返回处理后的 dict（可能原样返回——未超预算时零改动，零拷贝开销）。
    """
    if not previous_outputs or not _enabled():
        return previous_outputs or {}

    budget = _budget()
    if budget <= 0:
        return previous_outputs

    meta = meta or {}

    # 无输出超预算 → 原样返回（快路径：大多数轮次走这里）
    entries = [
        (dep_id, output, _count_output_tokens(output))
        for dep_id, output in previous_outputs.items()
    ]
    total = sum(t for _, _, t in entries)
    if total <= budget:
        return previous_outputs

    before_tokens = total
    from backend.memory.token_budget import count_tokens

    # 最新结果优先：dict 插入序的尾部视为最新（scheduler 按 edges 顺序构造），
    # 从最新往回完整保留，放不下的旧条目降级为摘要结构
    compacted: dict[str, Any] = {}
    used = 0
    overflow = False
    for dep_id, output, tokens in reversed(entries):
        if not overflow and used + tokens <= budget:
            used += tokens
            compacted[dep_id] = output
        else:
            overflow = True
            entry = _degrade_entry(dep_id, output, meta.get(dep_id))
            entry_tokens = count_tokens(serialize_for_count(entry))
            # 降级条目预算挤不下时收缩其 preview（下限 32，保证至少有摘要可读）
            while entry_tokens > max(0, budget - used) and _PREVIEW_DEGRADE_TOKENS > 32:
                entry = _degrade_entry(
                    dep_id, output, meta.get(dep_id), preview_tokens=32)
                entry_tokens = count_tokens(serialize_for_count(entry))
                break
            compacted[dep_id] = entry
            used += entry_tokens
    # 恢复原插入序
    result = {dep_id: compacted[dep_id] for dep_id, _, _ in entries if dep_id in compacted}

    after_tokens = count_tokens(serialize_for_count(result))
    _record_compaction(
        level="L3", action="previous_outputs_compact",
        before_tokens=before_tokens, after_tokens=after_tokens,
    )
    from backend.context_budget.metrics import emit_context_event
    emit_context_event(
        level="L3", action="previous_outputs_compact",
        before_tokens=before_tokens, after_tokens=after_tokens,
    )
    return result
