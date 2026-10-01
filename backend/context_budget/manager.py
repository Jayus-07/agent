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
from dataclasses import dataclass
from datetime import datetime
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
        rag_scores: list[float] | None = None,
        rag_sources: list[str] | None = None,
        predicted_extra_tokens: int = 0,
        pins: Any | None = None,
    ) -> PreparedContext:
        """LLM 调用前的统一检查入口（同步，规格 §九）。

        extra_reserved_tokens：调用方折算的非消息占用（tools schema /
        response_format / provider 信封），直接从预算中扣除。
        rag_scores / rag_sources：RAG 证据相关性口径（P2-1）——提供时
        hard trim 走 RAGBudgeter（价值优先 + source 多样性），否则原序裁剪。
        predicted_extra_tokens：预测的后续注入（P2-2，如下一步
        previous_outputs），只参与 L4/L5 触发判定，不参与裁剪目标。
        pins：PinnedContext（生产收口 B4）——业务级显式 pin（确认态/
        业务实体内容锚定），与自动 pin 并集参与 L2 裁剪豁免。

        确定性裁剪（优先级：旧 history → RAG 证据 → 旧 previous_outputs），
        SystemMessage 与语义 pin 消息始终保留。全部裁剪后仍超限：
        warning + metric +1 + overflow 标记（调用方硬门禁拒发）。

        STOP B（2026-10-01）：本入口的 L5 语义 = worker 线程内联等待；
        检测到运行中的事件循环时不等待、不另起后台任务（异步在线调用
        必须走 prepare_llm_context_async——「移除请求循环上的裸
        create_task」）。
        """
        state = self._prepare_deterministic(
            messages=messages, previous_outputs=previous_outputs,
            rag_context=rag_context, extra_reserved_tokens=extra_reserved_tokens,
            rag_scores=rag_scores, rag_sources=rag_sources,
            predicted_extra_tokens=predicted_extra_tokens, pins=pins)
        msgs, used = self._maybe_auto_compact(
            state.msgs, state.used, state.budget,
            predicted_extra_tokens=state.predicted)
        return state.finalize(msgs, used, overflow=used > state.budget)

    async def prepare_llm_context_async(
        self,
        *,
        messages: list | None = None,
        previous_outputs: dict[str, Any] | None = None,
        rag_context: list[str] | None = None,
        extra_reserved_tokens: int = 0,
        rag_scores: list[float] | None = None,
        rag_sources: list[str] | None = None,
        predicted_extra_tokens: int = 0,
        pins: Any | None = None,
    ) -> PreparedContext:
        """异步在线入口（STOP B 2026-10-01）：与同步入口共享同一份确定性
        预检核心（L2→L4→硬裁）与最终预算口径；差异仅在 L5——在线路径
        「有时限地等待同轮摘要」（CONTEXT_L5_ONLINE_WAIT_SECONDS，受摘要
        实测 P95 约束而非 30s 死等上限），成功才重建本轮 projection，
        失败/超时沿用确定性结果（摘要已落库，下一轮生效）。"""
        state = self._prepare_deterministic(
            messages=messages, previous_outputs=previous_outputs,
            rag_context=rag_context, extra_reserved_tokens=extra_reserved_tokens,
            rag_scores=rag_scores, rag_sources=rag_sources,
            predicted_extra_tokens=predicted_extra_tokens, pins=pins)
        msgs, used = await self._maybe_auto_compact_async(
            state.msgs, state.used, state.budget,
            predicted_extra_tokens=state.predicted)
        return state.finalize(msgs, used, overflow=used > state.budget)

    def _prepare_deterministic(
        self,
        *,
        messages: list | None,
        previous_outputs: dict[str, Any] | None,
        rag_context: list[str] | None,
        extra_reserved_tokens: int,
        rag_scores: list[float] | None,
        rag_sources: list[str] | None,
        predicted_extra_tokens: int,
        pins: Any | None,
    ) -> "_PrepareState":
        """共享确定性预检核心（零 LLM、零 IO 阻塞）：L2 → L4 → 硬裁。
        同步/异步入口的唯一公共路径，保证两入口预算口径一致。"""
        from backend.memory.token_budget import (
            count_tokens,
            trim_texts_to_budget,
        )
        from backend.context_budget.micro_compactor import compact_previous_outputs

        budget = self.get_input_budget(
            extra_reserved_tokens=extra_reserved_tokens)
        predicted = max(0, int(predicted_extra_tokens or 0))

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

        # L2：动态历史预算裁剪（历史预算 = 总预算 - po - rag；语义 pin 永不丢）。
        # history_cap == 0 = 历史没有空间（不是关闭裁剪）：仍执行裁剪，
        # 只保留 System 与语义 pin（2026-10-01 STOP A 语义统一）。
        history_cap = self.history_budget(
            reserved_tokens=po_tokens + rag_tokens)
        if msgs:
            msgs, dropped = _trim_semantic(msgs, history_cap, pins=pins)
            if dropped:
                _record_trim(dropped, msgs, list(messages or []))
                msg_tokens = sum(_count_message(m) for m in msgs)

        used = msg_tokens + po_tokens + rag_tokens

        # ── L4 Context Collapse（零 LLM、确定性、可回滚）─────────────
        # 触发：predicted_usage_ratio >= CONTEXT_L4_TRIGGER_RATIO（默认 0.80）。
        # 滞回（P2-2）：折叠后仍高于 CONTEXT_L4_TARGET_RATIO 时逐步收紧
        # 保留轮数（下限 1），一次触发压到安全区，避免下一轮立即重触发。
        folds: list = []
        if budget > 0 and (used + predicted) / budget >= float(
                _cfg("CONTEXT_L4_TRIGGER_RATIO", 0.80)):
            from backend.context_budget.collapse import fold_messages
            from backend.context_budget.metrics import (
                emit_context_event,
                record_compaction,
            )

            target = float(_cfg("CONTEXT_L4_TARGET_RATIO", 0.65))
            keep = int(_cfg("CONTEXT_L4_KEEP_RECENT_TURNS", 4))
            while keep >= 1:
                folded, fold = fold_messages(msgs, keep_recent_turns=keep)
                if fold is None:
                    break
                used_before = used
                msgs = folded
                folds.append(fold.to_dict())
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
                    f"saved_tokens={max(0, used_before - used)} "
                    f"keep_recent_turns={keep}")
                if used / budget <= target:
                    break
                keep -= 1  # 仍高于目标比例 → 更激进折叠（滞回）

        # ── 确定性裁剪（仍超限时）：旧 history → RAG 证据 → 旧 previous_outputs ──
        # 1) 收紧 history：预算 = 剩余空间（可为 0 = 只留保护项；语义 pin 全保留）。
        # 预算内时 cap ≥ 当前用量，裁剪自然零丢弃（与旧「预算内短路」等价）
        remaining = budget - po_tokens - rag_tokens
        if msgs:
            msgs, dropped = _trim_semantic(msgs, max(0, remaining), pins=pins)
            if dropped:
                _record_trim(dropped, msgs, list(messages or []))

        used = sum(_count_message(m) for m in msgs) + po_tokens + rag_tokens

        # 2) RAG 证据收缩（P2-1：有分数走 RAGBudgeter 价值优先+多样性；
        #    无分数保持原序从头保留）
        if used > budget and rag_texts:
            rag_budget = budget - sum(_count_message(m) for m in msgs) - po_tokens
            if rag_scores:
                from backend.context_budget.rag_budgeter import budget_rag_texts
                rag_texts, _dropped = budget_rag_texts(
                    rag_texts, max(0, rag_budget),
                    scores=rag_scores, sources=rag_sources)
            else:
                rag_texts, _dropped = trim_texts_to_budget(
                    rag_texts, max(0, rag_budget))
            rag_tokens = count_tokens("\n".join(rag_texts))
            used = sum(_count_message(m) for m in msgs) + po_tokens + rag_tokens

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

        # L5 不在确定性核心内：由同步/异步入口各自触发执行（STOP B），
        # 触发判定统一在 _l5_gate（0.90~1.0 与 >100% 同一入口）
        return _PrepareState(
            msgs=msgs, po=po, rag_texts=rag_texts, folds=folds,
            used=used, budget=budget, predicted=predicted,
            extra_reserved_tokens=max(0, int(extra_reserved_tokens or 0)))

    # ── L5 触发与执行（STOP B 2026-10-01：SummaryFlight 单飞）──────
    # 进程内单飞 = auto_compact.start_summary_flight（键=租户+会话，
    # 槽位持有直到底层摘要线程真正结束）；跨进程单飞在
    # run_incremental_summary 的 Redis 锁 + 水位线 CAS 兜底。

    def _maybe_auto_compact(
        self, msgs: list, used: int, budget: int,
        predicted_extra_tokens: int = 0,
    ) -> tuple[list, int]:
        """同步入口的 L5 触发与执行。

        worker 线程（无事件循环）：经 SummaryFlight 内联等待摘要完成后
        重建 projection；运行中的事件循环上下文：不等待、不另起后台任务
        （请求循环上的裸 create_task 已移除——异步在线调用必须走
        prepare_llm_context_async；下一轮由 end_turn 增量摘要补充）。
        返回 (可能重建后的消息列表, 重算后用量)。失败/不触发原样返回。
        """
        session_id = self._l5_gate(msgs, used, budget, predicted_extra_tokens)
        if not session_id:
            return msgs, used
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            logger.debug(
                "[ContextBudget] L5 经同步入口命中事件循环：不等待、"
                "不另起后台任务，本轮用确定性结果（异步在线入口负责等待）")
            return msgs, used
        from backend.context_budget.auto_compact import (
            start_summary_flight,
            wait_flight_sync,
        )
        flight = start_summary_flight(session_id, extra_facts=_extra_facts())
        if flight is None:
            # 同键摘要已在底层线程中运行（含此前等待超时后的延续）
            logger.debug("[ContextBudget] L5 同键摘要已在执行，本轮用确定性结果")
            return msgs, used
        try:
            timeout = float(_cfg("CONTEXT_L5_SUMMARY_TIMEOUT_SECONDS", 30)) + 10
            outcome = wait_flight_sync(flight, timeout)
        except Exception:
            # L5 局部故障边界（STOP A）：任何异常都不得外溢——外溢会让
            # 调用方 preflight 整体失败并回退原始未裁剪消息
            logger.warning(
                "[ContextBudget] L5 等待异常，沿用确定性裁剪结果", exc_info=True)
            outcome = None
        return self._adopt_l5_outcome(msgs, used, budget, outcome)

    async def _maybe_auto_compact_async(
        self, msgs: list, used: int, budget: int,
        predicted_extra_tokens: int = 0,
    ) -> tuple[list, int]:
        """异步在线入口的 L5：有时限地等待同轮摘要。

        在线时限 = CONTEXT_L5_ONLINE_WAIT_SECONDS（默认 8s，按摘要实测
        P95 量级设定，非 30s provider 死等上限）。成功 → 本轮重建
        projection；超时/失败 → 确定性结果。摘要线程继续跑完落库供下一
        轮读取；单飞槽位持有到底层线程真正结束（等待方退出 ≠ 线程停止）。
        """
        session_id = self._l5_gate(msgs, used, budget, predicted_extra_tokens)
        if not session_id:
            return msgs, used
        from backend.context_budget.auto_compact import (
            start_summary_flight,
            wait_flight_async,
        )
        flight = start_summary_flight(session_id, extra_facts=_extra_facts())
        if flight is None:
            # 同键摘要已在底层线程中运行：本轮确定性结果 + deferred 留痕
            try:
                from backend.context_budget.metrics import (
                    emit_context_event,
                    record_l5_attempt,
                )
                record_l5_attempt(status="failed", reason="lock_conflict")
                emit_context_event(level="L5", action="deferred",
                                   before_tokens=used, after_tokens=used)
            except Exception:
                pass
            return msgs, used
        try:
            outcome = await wait_flight_async(
                flight, float(_cfg("CONTEXT_L5_ONLINE_WAIT_SECONDS", 8)))
        except Exception:
            logger.warning(
                "[ContextBudget] L5 在线等待异常，沿用确定性裁剪结果",
                exc_info=True)
            outcome = None
        return self._adopt_l5_outcome(msgs, used, budget, outcome)

    def _l5_gate(
        self, msgs: list, used: int, budget: int,
        predicted_extra_tokens: int,
    ) -> str | None:
        """L5 触发判定（同步/异步入口共用）。返回会话 id=放行；None=跳过。"""
        if budget <= 0 or (used + max(0, predicted_extra_tokens)) / budget \
                < float(_cfg("CONTEXT_L5_TRIGGER_RATIO", 0.90)):
            return None
        if not _cfg("CONTEXT_L5_ENABLED", True) or not _cfg(
                "CONTEXT_BUDGET_ENABLED", True):
            # kill switch 观测：disabled 计数（低基数，无 session 信息）
            try:
                from backend.context_budget.metrics import record_l5_attempt
                record_l5_attempt(status="disabled", reason="disabled")
            except Exception:
                pass
            return None
        from backend.context_budget.auto_compact import is_l5_active
        if is_l5_active():
            return None  # 摘要 LLM 自身的 preflight，禁止重入

        from backend.core.request_context import get_current_session_id
        session_id = get_current_session_id() or ""
        # 无真实会话上下文（测试/后台脚本/无 session 请求）不触发 L5：
        # 增量摘要水位线挂在 chat_sessions 上，没有会话无处落账。
        if session_id.strip() in ("", "default", "multi-agent-default"):
            logger.debug("[ContextBudget] L5 触发但无有效会话上下文，跳过")
            return None
        return session_id

    def _adopt_l5_outcome(self, msgs: list, used: int, budget: int,
                          outcome: Any) -> tuple[list, int]:
        """摘要结果的本轮采用：水位线一致性核对通过才重建 projection。

        核对不过 / 摘要失败 / 超时：摘要已由 run_incremental_summary 的
        CAS 落库供下一轮读取，本轮沿用确定性裁剪结果，绝不阻断请求。
        """
        from backend.context_budget.auto_compact import fold_rebuild
        from backend.context_budget.metrics import (
            emit_context_event,
            record_compaction,
            record_compaction_latency,
            record_summary_llm_tokens,
        )
        if outcome is None:
            # 安全回退：摘要失败/超时/无收益 → 沿用确定性裁剪结果，绝不阻断
            try:
                from backend.observability.metrics import degradation_alerts_total
                degradation_alerts_total.labels(
                    code="context_autocompact_failed", level="warn").inc()
            except Exception:
                pass
            return msgs, used

        record_compaction_latency(
            level="L5", seconds=max(0, outcome.latency_ms) / 1000.0)
        record_summary_llm_tokens(
            prompt_tokens=outcome.llm_prompt_tokens,
            completion_tokens=outcome.llm_completion_tokens)

        # 重建 active projection：旧历史 → 新摘要数据块（保留最近 N 轮）。
        # 滞回（P2-2）：若以默认轮数重建仍高于 CONTEXT_L5_TARGET_RATIO，
        # 逐步试探更小的保留轮数（下限 1），只用成功的最深一档提交。
        old_msg_tokens = sum(_count_message(m) for m in msgs)
        projection_meta = {
            "through_message_id": outcome.through_id,
            "summary_token_count": outcome.token_count,
            "delta_messages": outcome.delta_message_count,
            "protected_facts": outcome.protected_fact_count,
            "created_at": datetime.utcnow().isoformat(),
        }
        target = float(_cfg("CONTEXT_L5_TARGET_RATIO", 0.70))
        keep = int(_cfg("CONTEXT_L4_KEEP_RECENT_TURNS", 4))
        rebuilt, replaced, boundary = fold_rebuild(
            msgs, outcome.summary, keep_recent_turns=keep,
            projection_meta=projection_meta)
        while replaced > 0 and budget > 0 and keep > 1:
            new_used = max(0, used - old_msg_tokens
                           + sum(_count_message(m) for m in rebuilt))
            if new_used / budget <= target:
                break  # 本档已压到目标区
            cand_rebuilt, cand_replaced, cand_boundary = fold_rebuild(
                msgs, outcome.summary, keep_recent_turns=keep - 1,
                projection_meta=projection_meta)
            if cand_replaced <= 0:
                break  # 更深一档无可替换，保持当前档
            keep -= 1
            rebuilt, replaced, boundary = (cand_rebuilt, cand_replaced,
                                           cand_boundary)
        if replaced > 0 and not self._summary_covers_projection(
                outcome, msgs, boundary):
            # 水位线一致性核对未过（STOP B #3）：摘要只落库供下一轮，
            # 本轮继续用确定性结果，避免用覆盖不足的摘要替换头部
            logger.info(
                "[ContextBudget] L5 水位线一致性核对未过，本轮不采用摘要"
                f"（through={outcome.through_id} "
                f"boundary={outcome.boundary_id}），沿用确定性结果")
            return msgs, used
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
                f"through_message_id={outcome.through_id} "
                f"(目标比例 {target})")
        else:
            # 摘要已落库但本轮消息层无可替换内容（罕见）：下一轮生效
            logger.info(
                "[ContextBudget] L5 摘要已落库，本轮 projection 无可替换历史")
        return msgs, used

    @staticmethod
    def _summary_covers_projection(outcome: Any, msgs: list,
                                   boundary_idx: int | None) -> bool:
        """水位线一致性核对（STOP B #3）：摘要覆盖范围 ⊇ 将被替换的头部。

        active projection 的头部来自 DB 历史（start_session 注入），替换
        范围的最后一行 ≤ 装载时点「最近第 N 轮 user 消息 id」≤ 摘要时点
        同一边界（boundary 只随消息追加增大）≤ through_id——结构性论证；
        消息若携带 db_message_id 则升级为逐条硬核对。
        """
        if outcome is None or getattr(outcome, "through_id", 0) <= 0 \
                or getattr(outcome, "delta_message_count", 0) <= 0:
            return False
        if boundary_idx is None:
            return False
        ids = []
        for m in msgs[:boundary_idx]:
            kw = getattr(m, "additional_kwargs", None)
            mid = kw.get("db_message_id") if isinstance(kw, dict) else None
            if mid is not None:
                ids.append(int(mid))
        if ids:
            return max(ids) <= int(outcome.through_id)
        return True

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

def _extra_facts() -> list:
    """请求级业务 pin 值（确认态/实体）→ critical 事实（生产收口 B4）。

    在 start_summary_flight 之前读取（随调用方上下文），摘要后确定性
    校验保真；读取失败不阻断（空列表降级）。
    """
    try:
        from backend.context_budget.pin import request_pin_values
        return [(kind, value) for value, kind in request_pin_values()]
    except Exception:
        return []


def _usage_components(m: list, p: dict, r: list[str],
                      extra_reserved_tokens: int) -> dict:
    """分项用量快照（指标口径；溢出对比只取非 tool_schema 项）。"""
    from backend.memory.token_budget import count_tokens
    return {
        "system": sum(_count_message(x) for x in m
                      if type(x).__name__ == "SystemMessage"),
        "history": sum(_count_message(x) for x in m
                       if type(x).__name__ != "SystemMessage"),
        "previous_outputs": count_tokens("\n".join(
            _serialize_po(v) for v in p.values() if v is not None)),
        "rag": count_tokens("\n".join(r)) if r else 0,
        "tool_schema": max(0, int(extra_reserved_tokens or 0)),
    }


@dataclass
class _PrepareState:
    """确定性预检核心的产出快照（L5 前），同步/异步入口共用 finalize。"""

    msgs: list
    po: dict
    rag_texts: list
    folds: list
    used: int
    budget: int
    predicted: int
    extra_reserved_tokens: int

    def finalize(self, m: list, used: int, *,
                 overflow: bool = False) -> PreparedContext:
        from backend.context_budget.metrics import record_overflow
        comps = _usage_components(m, self.po, self.rag_texts,
                                  self.extra_reserved_tokens)
        # tool_schema（extra_reserved_tokens）已在 get_input_budget 中
        # 从预算扣除，用量对比不得再累加一次（双重扣除会把未超窗
        # 误判成超窗）；schema 分项仍单独进指标
        comparable = sum(v for k, v in comps.items() if k != "tool_schema")
        usage = ContextUsage(
            used_tokens=comparable,
            input_budget=self.budget,
            remaining_tokens=max(0, self.budget - comparable),
            usage_ratio=(comparable / self.budget) if self.budget > 0 else 0.0,
        )
        try:
            from backend.context_budget.metrics import (
                record_usage_components,
            )
            record_usage_components(**comps)
        except Exception:
            pass
        if overflow:
            logger.warning(
                f"[ContextBudget] preflight 后仍超 hard budget: "
                f"used={comparable} budget={self.budget} components={comps}"
                f"（已最大化裁剪；调用方硬门禁必须拒绝发送，"
                f"provider 调用次数为 0）"
            )
            record_overflow("preflight")
        return PreparedContext(
            messages=m, previous_outputs=self.po,
            rag_context=self.rag_texts or None,
            usage=usage, overflow=overflow, folds=self.folds or [],
        )


def _trim_semantic(msgs: list, cap: int,
                   pins: Any | None = None) -> tuple[list, int]:
    """L2 裁剪（2026-09-23 P1-2 Semantic Pin 版）。

    不再依赖「最后一条 = 当前问题」的位置假设：pin 由
    context_budget.pin.collect_pin_indices 语义判定——SystemMessage、
    最后一条 HumanMessage（当前问题）、活跃 tool call 对永不丢弃；
    assistant(tool_calls)+ToolMessage 整组原子保留/丢弃。
    pins（生产收口 B4）：PinnedContext 显式业务 pin（内容锚定确认态/
    实体），与自动 pin 并集豁免。
    """
    if not msgs:
        return msgs, 0
    from backend.context_budget.pin import collect_pin_indices, pins_from_request
    from backend.memory.token_budget import trim_messages_to_budget
    if pins is None:
        pins = pins_from_request()
    pin_indices = collect_pin_indices(msgs)
    if pins is not None:
        pin_indices = pin_indices | pins.resolve(msgs)
    return trim_messages_to_budget(msgs, cap, pin_indices=pin_indices)


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

    if budget <= 0:
        # 零预算 = 没有空间：真正清空（2026-10-01 STOP A），不是保留原样。
        # 降级后的最小摘要条目也占 token，放不下就是放不下。
        if po:
            logger.warning(
                "[ContextBudget] previous_outputs 剩余空间为 0，整体清空"
                f"（原 {len(po)} 条）")
        return {}
    if not po:
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
