"""context_budget.manager — ContextBudgetManager 统一预算管理器

基础版职责（完整规格见 docs/2026-09-22-context-budget-management-实施规格.md）：
  - get_input_budget():  input = LLM_CONTEXT_LENGTH - 输出预留 - 安全余量
  - calculate_usage():   统一用量计算入口（业务层禁止自行重复计算）
  - history_budget():    L2 动态历史预算（HISTORY_TOKEN_BUDGET 只是上限）
  - prepare_llm_context(): 统一 Prompt Preflight（L2 trim → L4 collapse →
    确定性 hard trim → L5 AutoCompact 触发判定与执行）

原则：
  - 所有阈值以 token 为准（count_tokens，tiktoken）
  - 只影响发送给模型的 active context，绝不触碰原始 chat_messages
  - System Prompt / 当前用户问题 / 最新必要业务状态永远保留
"""

from __future__ import annotations

import asyncio
import threading
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

    def get_input_budget(self, *, extra_reserved_tokens: int = 0) -> int:
        """active context 总预算 = min(配置窗口, 模型注册窗口) - 输出预留
        - 安全余量 - 额外预留（tools schema / response_format 等）。

        模型窗口经 token_counter.resolve_model_context_window 取
        llm_models.context_length 与配置窗口的较小值；未注册模型回退
        配置窗口（行为兼容旧版）。
        """
        from backend.context_budget.token_counter import (
            resolve_model_context_window,
        )
        return max(
            0,
            int(resolve_model_context_window())
            - int(_cfg("CONTEXT_OUTPUT_RESERVE_TOKENS", 768))
            - int(_cfg("CONTEXT_SAFETY_RESERVE_TOKENS", 256))
            - max(0, int(extra_reserved_tokens)),
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
        extra_reserved_tokens: int = 0,
    ) -> PreparedContext:
        """LLM 调用前的统一检查入口（第一版流程，规格 §九）：

          L2 history trim → L3 previous_outputs compact → 复核 count_tokens
          → 确认 <= input_budget

        extra_reserved_tokens：调用方折算的非消息占用（tools schema /
        response_format / provider 信封），直接从预算中扣除。

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

        budget = self.get_input_budget(
            extra_reserved_tokens=extra_reserved_tokens)

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
            folds: list | None = None,
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
                usage=usage, overflow=overflow, folds=folds or [],
            )

        # L2：动态历史预算裁剪（历史预算 = 总预算 - po - rag；最后一条消息永不丢）
        history_cap = self.history_budget(
            reserved_tokens=po_tokens + rag_tokens)
        if history_cap > 0:
            msgs, dropped = _trim_semantic(msgs, history_cap)
            if dropped:
                _record_trim(dropped, msgs, list(messages or []))
                msg_tokens = sum(_count_message(m) for m in msgs)

        used = msg_tokens + po_tokens + rag_tokens

        # ── L4 Context Collapse（零 LLM、确定性、可回滚）─────────────
        # 触发：usage_ratio >= CONTEXT_L4_TRIGGER_RATIO（默认 0.80）。
        # 折叠较早普通历史为 Projection SystemMessage；SystemMessage /
        # 最近 CONTEXT_L4_KEEP_RECENT_TURNS 轮 / 当前消息永不折叠；
        # 原始 chat_messages 不受影响。仍超限时继续走确定性 hard trim。
        folds: list = []
        if budget > 0 and (used / budget) >= float(
                _cfg("CONTEXT_L4_TRIGGER_RATIO", 0.80)):
            from backend.context_budget.collapse import fold_messages
            from backend.context_budget.metrics import (
                emit_context_event,
                record_compaction,
            )

            folded, fold = fold_messages(msgs)
            if fold is not None:
                used_before = used
                msgs = folded
                folds = [fold.to_dict()]
                msg_tokens = sum(_count_message(m) for m in msgs)
                used = msg_tokens + po_tokens + rag_tokens
                record_compaction(level="L4", action="collapse",
                                  before_tokens=used_before,
                                  after_tokens=used)
                emit_context_event(level="L4", action="collapse",
                                   before_tokens=used_before,
                                   after_tokens=used,
                                   reversible=True,
                                   fold_id=fold.fold_id,
                                   folded_messages=fold.message_count)
                logger.info(
                    f"context_compacted level=L4 action=collapse "
                    f"fold_id={fold.fold_id} folded_messages={fold.message_count} "
                    f"before_tokens={used_before} after_tokens={used} "
                    f"saved_tokens={max(0, used_before - used)}")

        if used <= budget:
            # 已在预算内：仍须做 L5 触发判定（0.90~1.0 区间属 L5 职责，
            # 不触发确定性 hard trim——那是 >100% 的兜底）
            msgs, used = self._maybe_auto_compact(msgs, used, budget)
            return _finalize(msgs, po, rag_texts, folds=folds)

        # ── 确定性裁剪（仍超限时）：旧 history → 旧 previous_outputs → RAG 尾部 ──
        # 1) 收紧 history：预算 = 剩余空间（SystemMessage 由 trim 保证全保留，
        #    最新消息从尾部优先保留）
        remaining = budget - po_tokens - rag_tokens
        if remaining > 0 and msgs:
            msgs, dropped = _trim_semantic(msgs, remaining)
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

        # ── L5 AutoCompact（最后一道防线，2026-09-22 Phase 3）──────────
        # 触发链路：L1/L2/L3/L4 全部执行 → 重算用量 → 仍 >= 0.90 才触发。
        # 摘要失败/超时安全回退：沿用上面确定性裁剪结果，绝不阻断请求。
        msgs, used = self._maybe_auto_compact(msgs, used, budget)

        return _finalize(msgs, po, rag_texts, overflow=used > budget,
                         folds=folds)

    # ── L5 触发与执行 ───────────────────────────────────────────

    # 同会话单飞：防止并发请求对同一 session 重复触发摘要 LLM 调用
    # （进程内第一层；跨进程单飞在 run_incremental_summary 的 Redis 锁 +
    #   水位线 CAS 兜底，2026-09-23 STOP C）
    _l5_inflight: set[str] = set()
    _l5_inflight_lock = threading.Lock()

    def _maybe_auto_compact(
        self, msgs: list, used: int, budget: int,
    ) -> tuple[list, int]:
        """usage_ratio 达到 CONTEXT_L5_TRIGGER_RATIO 时触发 L5 并重建 projection。

        返回 (可能重建后的消息列表, 重算后用量)。失败/不触发原样返回。
        """
        if budget <= 0 or used / budget < float(
                _cfg("CONTEXT_L5_TRIGGER_RATIO", 0.90)):
            return msgs, used
        if not _cfg("CONTEXT_L5_ENABLED", True) or not _cfg(
                "CONTEXT_BUDGET_ENABLED", True):
            # kill switch 观测：disabled 计数（低基数，无 session 信息）
            try:
                from backend.context_budget.metrics import record_l5_attempt
                record_l5_attempt(status="disabled", reason="disabled")
            except Exception:
                pass
            return msgs, used
        from backend.context_budget.auto_compact import is_l5_active
        if is_l5_active():
            return msgs, used  # 摘要 LLM 自身的 preflight，禁止重入

        from backend.core.request_context import get_current_session_id
        session_id = get_current_session_id() or ""
        # 无真实会话上下文（测试/后台脚本/无 session 请求）不触发 L5：
        # 增量摘要水位线挂在 chat_sessions 上，没有会话无处落账。
        if session_id.strip() in ("", "default", "multi-agent-default"):
            logger.debug("[ContextBudget] L5 触发但无有效会话上下文，跳过")
            return msgs, used
        with self._l5_inflight_lock:
            if session_id in self._l5_inflight:
                return msgs, used  # 同会话已有摘要在进行，本轮先用裁剪结果
            self._l5_inflight.add(session_id)
        try:
            return self._run_l5(msgs, used, budget, session_id)
        finally:
            with self._l5_inflight_lock:
                self._l5_inflight.discard(session_id)

    def _run_l5(self, msgs: list, used: int, budget: int,
                session_id: str) -> tuple[list, int]:
        import time as _time

        from backend.context_budget.auto_compact import (
            SyncMemorySummaryStore,
            fold_rebuild,
            run_incremental_summary,
        )
        from backend.context_budget.metrics import (
            emit_context_event,
            record_compaction,
            record_compaction_latency,
            record_summary_llm_tokens,
        )

        # 事件循环上下文（async 节点内调用）：同步 LLM 摘要不能阻塞 loop，
        # 降级为 fire-and-forget——本轮继续用裁剪结果，摘要落库后下一轮生效。
        try:
            loop = asyncio.get_running_loop()
            import backend.context_budget.auto_compact as _ac
            _t = loop.create_task(_ac.run_auto_compact_async(session_id))
            _L5_TASKS.add(_t)
            _t.add_done_callback(_L5_TASKS.discard)
            logger.info(
                "[ContextBudget] L5 触发（async 上下文）→ 后台摘要，本轮安全降级")
            emit_context_event(level="L5", action="deferred",
                               before_tokens=used, after_tokens=used)
            return msgs, used
        except RuntimeError:
            pass  # 无 running loop：worker 线程同步路径，可内联执行

        started = _time.perf_counter()
        outcome = run_incremental_summary(
            session_id, SyncMemorySummaryStore(session_id))
        record_compaction_latency(
            level="L5", seconds=_time.perf_counter() - started)

        if outcome is None:
            # 安全回退：摘要失败/无收益 → 沿用确定性裁剪结果，绝不阻断
            try:
                from backend.observability.metrics import degradation_alerts_total
                degradation_alerts_total.labels(
                    code="context_autocompact_failed", level="warn").inc()
            except Exception:
                pass
            return msgs, used

        record_summary_llm_tokens(
            prompt_tokens=outcome.llm_prompt_tokens,
            completion_tokens=outcome.llm_completion_tokens)

        # 重建 active projection：旧历史 → 新摘要 SystemMessage（保留最近 N 轮）
        old_msg_tokens = sum(_count_message(m) for m in msgs)
        rebuilt, replaced, _boundary = fold_rebuild(msgs, outcome.summary)
        if replaced > 0:
            used_before = used
            msgs = rebuilt
            used = max(0, used - old_msg_tokens
                       + sum(_count_message(m) for m in msgs))
            record_compaction(level="L5", action="auto_compact",
                              before_tokens=used_before, after_tokens=used)
            emit_context_event(level="L5", action="auto_compact",
                               before_tokens=used_before, after_tokens=used,
                               replaced_messages=replaced,
                               summary_tokens=outcome.token_count,
                               delta_messages=outcome.delta_message_count)
            logger.info(
                f"context_compacted level=L5 action=auto_compact "
                f"replaced_messages={replaced} "
                f"before_tokens={used_before} after_tokens={used} "
                f"saved_tokens={max(0, used_before - used)} "
                f"(目标比例 {float(_cfg('CONTEXT_L5_TARGET_RATIO', 0.70))})")
        else:
            # 摘要已落库但本轮消息层无可替换内容（罕见）：下一轮生效
            logger.info(
                "[ContextBudget] L5 摘要已落库，本轮 projection 无可替换历史")
        return msgs, used

    # ── L4 / L5 预留接口（本版不实现，规格 §十）────────────────

    def should_context_collapse(self, usage: ContextUsage) -> bool:
        """L4 Context Collapse 触发判定。"""
        return usage.usage_ratio >= float(
            _cfg("CONTEXT_L4_TRIGGER_RATIO", 0.80))

    async def context_collapse(self, messages: list, **kwargs: Any):
        """L4: projection based context folding（零 LLM、非破坏性）。

        对给定消息列表执行确定性折叠，返回 (折叠后消息列表, ContextFold|None)。
        不修改原始 chat history；调用方决定是否采用（通常经 prepare_llm_context
        自动触发，本方法供显式调用/恢复编排使用）。
        """
        from backend.context_budget.collapse import fold_messages
        return fold_messages(messages)

    def should_auto_compact(self, usage: ContextUsage) -> bool:
        """L5 AutoCompact 触发判定。"""
        return usage.usage_ratio >= float(
            _cfg("CONTEXT_L5_TRIGGER_RATIO", 0.90))

    async def auto_compact(self, *, session_id: str, **kwargs: Any):
        """L5 AutoCompact：增量摘要一次（显式调用入口）。

        正常路径经 prepare_llm_context 的触发链路（L1-L4 之后）自动进入；
        本方法供管理端/测试显式触发。失败返回 None，旧摘要与水位线不动。
        """
        from backend.context_budget.auto_compact import (
            SyncMemorySummaryStore,
            run_incremental_summary,
        )
        import asyncio as _aio
        return await _aio.to_thread(
            run_incremental_summary,
            session_id, SyncMemorySummaryStore(session_id))


# ── 模块级辅助 ──────────────────────────────────────────────────

def _trim_semantic(msgs: list, cap: int) -> tuple[list, int]:
    """L2 裁剪（2026-09-23 P1-2 Semantic Pin 版）。

    不再依赖「最后一条 = 当前问题」的位置假设：pin 由
    context_budget.pin.collect_pin_indices 语义判定——SystemMessage、
    最后一条 HumanMessage（当前问题）、活跃 tool call 对永不丢弃；
    assistant(tool_calls)+ToolMessage 整组原子保留/丢弃。
    """
    if not msgs:
        return msgs, 0
    from backend.context_budget.pin import collect_pin_indices
    from backend.memory.token_budget import trim_messages_to_budget
    return trim_messages_to_budget(
        msgs, cap, pin_indices=collect_pin_indices(msgs))


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
