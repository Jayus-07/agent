"""context_budget.manager — ContextBudgetManager 统一预算管理器

基础版职责（完整规格见 docs/2026-09-22-context-budget-management-实施规格.md）：
  - get_input_budget():  input = LLM_CONTEXT_LENGTH - 输出预留 - 安全余量
  - calculate_usage():   统一用量计算入口（业务层禁止自行重复计算）
  - history_budget():    L2 动态历史预算（HISTORY_TOKEN_BUDGET 只是上限）
  - prepare_llm_context(): 统一 Prompt Preflight（L2 trim → L3 compact → 复核）
  - should_context_collapse / context_collapse:   L4 预留接口（不实现）
  - should_auto_compact / auto_compact:           L5 预留接口（不实现）

原则：
  - 所有阈值以 token 为准（count_tokens，tiktoken）
  - 只影响发送给模型的 active context，绝不触碰原始 chat_messages
  - System Prompt / 当前用户问题 / 最新必要业务状态永远保留
"""

from __future__ import annotations

from typing import Any

from backend.context_budget.models import ContextUsage, PreparedContext
from backend.shared.logger import logger


def _cfg(name: str, default: int | float) -> int | float:
    """从 config 模块读配置（业务代码禁止直接 os.getenv，统一走 config）。"""
    import backend.config as config
    return getattr(config, name, default)


class ContextBudgetManager:
    """统一上下文预算入口。无状态（阈值实时读 config，env 覆盖即时生效）。"""

    # ── 预算计算 ────────────────────────────────────────────────

    def get_input_budget(self) -> int:
        """active context 总预算 = 模型窗口 - 输出预留 - 安全余量。

        默认配置：4096 - 768 - 256 = 3072。
        """
        from backend.config.llm import LLM_CONTEXT_LENGTH
        return max(
            0,
            int(LLM_CONTEXT_LENGTH)
            - int(_cfg("CONTEXT_OUTPUT_RESERVE_TOKENS", 768))
            - int(_cfg("CONTEXT_SAFETY_RESERVE_TOKENS", 256)),
        )

    def calculate_usage(
        self,
        *,
        messages: list | None = None,
        extra_texts: list[str] | None = None,
    ) -> ContextUsage:
        """统一用量计算：messages（LangChain 消息）+ extra_texts（
        previous_outputs 序列化文本 / RAG 证据等）→ ContextUsage。
        """
        from backend.memory.token_budget import (
            count_message_tokens,
            count_tokens,
        )

        used = 0
        for msg in (messages or []):
            used += count_message_tokens(msg)
        for text in (extra_texts or []):
            used += count_tokens(text)

        budget = self.get_input_budget()
        return ContextUsage(
            used_tokens=used,
            input_budget=budget,
            remaining_tokens=max(0, budget - used),
            usage_ratio=(used / budget) if budget > 0 else 0.0,
        )

    def history_budget(
        self,
        *,
        system_tokens: int = 0,
        current_query_tokens: int = 0,
        reserved_tokens: int = 0,
    ) -> int:
        """L2 动态历史预算：HISTORY_TOKEN_BUDGET 是上限，不是每次的配额。

        available = input_budget - system - 当前问题 - 其他预留
        （previous_outputs / RAG 等由调用方折算进 reserved_tokens）
        history_budget = min(HISTORY_TOKEN_BUDGET, max(0, available))
        """
        from backend.config import HISTORY_TOKEN_BUDGET
        available = (
            self.get_input_budget()
            - int(system_tokens)
            - int(current_query_tokens)
            - int(reserved_tokens)
        )
        return min(int(HISTORY_TOKEN_BUDGET), max(0, available))

    # ── 统一 Prompt Preflight ───────────────────────────────────

    def prepare_llm_context(
        self,
        *,
        messages: list | None = None,
        previous_outputs: dict[str, Any] | None = None,
        rag_context: list[str] | None = None,
    ) -> PreparedContext:
        """LLM 调用前的统一检查入口（第一版流程，规格 §九）：

          L2 history trim → L3 previous_outputs compact → 复核 count_tokens
          → 确认 <= input_budget

        仍超 hard budget 时做确定性裁剪（优先级：旧 history → 旧
        previous_outputs → RAG 尾部证据），SystemMessage 与最新消息始终
        保留。全部裁剪后仍超限：warning + metric +1 + overflow 标记
        （安全降级——调用方拿到的是最大程度压缩后的结果，不做静默超限）。
        """
        from backend.memory.token_budget import (
            count_tokens,
            trim_messages_to_budget,
            trim_texts_to_budget,
        )
        from backend.context_budget.micro_compactor import compact_previous_outputs
        from backend.context_budget.metrics import record_overflow

        budget = self.get_input_budget()

        # L3：previous_outputs 总预算压缩
        po = compact_previous_outputs(previous_outputs or {})
        po_tokens = count_tokens("\n".join(
            _serialize_po(v) for v in po.values() if v is not None
        ))

        # RAG 证据：先整体留痕（确定性裁剪阶段再按剩余空间收缩）
        rag_texts = list(rag_context or [])
        rag_tokens = count_tokens("\n".join(rag_texts))

        msgs = list(messages or [])
        msg_tokens = sum(
            _count_message(m) for m in msgs
        )

        def _finalize(
            m: list, p: dict, r: list[str], *, overflow: bool = False,
        ) -> PreparedContext:
            used = (
                sum(_count_message(x) for x in m)
                + count_tokens("\n".join(_serialize_po(v) for v in p.values() if v is not None))
                + (count_tokens("\n".join(r)) if r else 0)
            )
            usage = ContextUsage(
                used_tokens=used,
                input_budget=budget,
                remaining_tokens=max(0, budget - used),
                usage_ratio=(used / budget) if budget > 0 else 0.0,
            )
            if overflow:
                logger.warning(
                    f"[ContextBudget] preflight 后仍超 hard budget: "
                    f"used={used} budget={budget}（已最大化裁剪，安全降级放行）"
                )
                record_overflow("preflight")
            return PreparedContext(
                messages=m, previous_outputs=p, rag_context=r or None,
                usage=usage, overflow=overflow,
            )

        # L2：动态历史预算裁剪（历史预算 = 总预算 - po - rag；最后一条消息永不丢）
        history_cap = self.history_budget(
            reserved_tokens=po_tokens + rag_tokens)
        if history_cap > 0:
            msgs, dropped = _trim_keep_last(msgs, history_cap)
            if dropped:
                _record_trim(dropped, msgs, list(messages or []))
                msg_tokens = sum(_count_message(m) for m in msgs)

        used = msg_tokens + po_tokens + rag_tokens
        if used <= budget:
            return _finalize(msgs, po, rag_texts)

        # ── 确定性裁剪（仍超限时）：旧 history → 旧 previous_outputs → RAG 尾部 ──
        # 1) 收紧 history：预算 = 剩余空间（SystemMessage 由 trim 保证全保留，
        #    最新消息从尾部优先保留）
        remaining = budget - po_tokens - rag_tokens
        if remaining > 0 and msgs:
            msgs, dropped = _trim_keep_last(msgs, remaining)
            if dropped:
                _record_trim(dropped, msgs, list(messages or []))

        used = sum(_count_message(m) for m in msgs) + po_tokens + rag_tokens

        # 2) RAG 尾部证据丢弃（trim_texts_to_budget 从头保留，尾部整体丢）
        if used > budget and rag_texts:
            rag_budget = budget - sum(_count_message(m) for m in msgs) - po_tokens
            kept_rag, _dropped = trim_texts_to_budget(rag_texts, max(0, rag_budget))
            rag_texts = kept_rag
            used = sum(_count_message(m) for m in msgs) + po_tokens + count_tokens("\n".join(rag_texts))

        # 3) 旧 previous_outputs 再收缩（进一步降级，最新条目最后动）
        if used > budget and po:
            po_budget = (
                budget - sum(_count_message(m) for m in msgs)
                - count_tokens("\n".join(rag_texts))
            )
            po = _shrink_po(po, max(0, po_budget))
            used = (
                sum(_count_message(m) for m in msgs)
                + count_tokens("\n".join(_serialize_po(v) for v in po.values() if v is not None))
                + count_tokens("\n".join(rag_texts))
            )

        return _finalize(msgs, po, rag_texts, overflow=used > budget)

    # ── L4 / L5 预留接口（本版不实现，规格 §十）────────────────

    def should_context_collapse(self, usage: ContextUsage) -> bool:
        """L4 Context Collapse 触发判定。"""
        return usage.usage_ratio >= float(
            _cfg("CONTEXT_L4_TRIGGER_RATIO", 0.80))

    async def context_collapse(self, *args: Any, **kwargs: Any) -> None:
        """Future L4: projection based context folding.

        Do not modify raw chat history. 预留接口，暂未实现。
        """
        raise NotImplementedError(
            "L4 Context Collapse 未实现（基础版仅预留接口）")

    def should_auto_compact(self, usage: ContextUsage) -> bool:
        """L5 AutoCompact 触发判定。"""
        return usage.usage_ratio >= float(
            _cfg("CONTEXT_L5_TRIGGER_RATIO", 0.90))

    async def auto_compact(self, *args: Any, **kwargs: Any) -> None:
        """Future L5: LLM based summary.

        预留接口，暂未实现；未来接入点为 SessionMemory.summarize() /
        chat_sessions.summary，本版不做任何改造。
        """
        raise NotImplementedError(
            "L5 AutoCompact 未实现（基础版仅预留接口）")


# ── 模块级辅助 ──────────────────────────────────────────────────

def _trim_keep_last(msgs: list, cap: int) -> tuple[list, int]:
    """L2 裁剪但**永远保留最后一条消息**（当前问题/prompt）。

    trim_messages_to_budget 会把单独超预算的消息整条丢弃——对历史可接受，
    对最后一条（当前用户问题）不可接受。这里把最后一条先摘出来，只对
    较旧部分做预算裁剪，再拼回去。
    """
    if not msgs:
        return msgs, 0
    from backend.memory.token_budget import trim_messages_to_budget
    last = msgs[-1]
    older_budget = cap - _count_message(last)
    kept, dropped = trim_messages_to_budget(msgs[:-1], max(0, older_budget))
    return kept + [last], dropped


def _serialize_po(value: Any) -> str:
    from backend.context_budget.tool_guard import serialize_for_count
    return serialize_for_count(value)


def _count_message(msg: Any) -> int:
    from backend.memory.token_budget import count_message_tokens
    return count_message_tokens(msg)


def _record_trim(dropped: int, kept: list, original: list) -> None:
    """L2 裁剪留痕：metric + 日志 + SSE 事件（只动 active context）。"""
    try:
        from backend.context_budget.metrics import (
            emit_context_event,
            record_compaction,
        )
        from backend.memory.token_budget import (
            count_message_tokens,
        )
        before = sum(count_message_tokens(m) for m in original)
        after = sum(count_message_tokens(m) for m in kept)
        saved = max(0, before - after)
        record_compaction(level="L2", action="history_trim",
                          before_tokens=before, after_tokens=after)
        emit_context_event(level="L2", action="history_trim",
                           before_tokens=before, after_tokens=after)
        logger.info(
            f"context_compacted level=L2 action=history_trim "
            f"dropped_messages={dropped} saved_tokens={saved}")
    except Exception:
        logger.debug("L2 裁剪留痕失败", exc_info=True)


def _shrink_po(po: dict[str, Any], budget: int) -> dict[str, Any]:
    """把 previous_outputs 整体收缩到 budget 内（全部条目降级为最小摘要）。"""
    from backend.memory.token_budget import count_tokens
    from backend.context_budget.tool_guard import serialize_for_count
    from backend.context_budget.micro_compactor import _degrade_entry

    if budget <= 0 or not po:
        return po
    result: dict[str, Any] = {}
    used = 0
    for dep_id, output in po.items():
        entry = _degrade_entry(dep_id, output, None, preview_tokens=32)
        tokens = count_tokens(serialize_for_count(entry))
        if used + tokens <= budget:
            used += tokens
            result[dep_id] = entry
        else:
            # 极端场景：宁可丢这条的正文也不能爆窗（有日志留痕，非无声丢失）
            logger.warning(
                f"[ContextBudget] previous_outputs 条目 {dep_id} 收缩后仍放不下，已丢弃")
    return result


# 模块级单例（无状态对象，进程内共享安全）
context_budget = ContextBudgetManager()
