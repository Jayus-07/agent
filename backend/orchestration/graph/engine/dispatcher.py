"""
backend/orchestration/graph/engine/dispatcher.py — SSE 事件流消费、假打字机兜底与终态生命周期收尾
"""
from __future__ import annotations

import asyncio
import queue
import threading
import time
from typing import Any, Generator

from backend.core.request_context import RequestCancelled
from backend.observability.tracer import trace_collector
from backend.orchestration.graph.engine.preflight import (
    _ANSWER_EVENT,
    _funnel_note_resolved,
    _runner_error_event,
)
from backend.orchestration.graph.engine.trace_builder import (
    _count_rounds_from_results,
    _end_root,
    _fallback_summary_from_results,
    _persist_cs_turn_if_needed,
    trace_from_state,
)
from backend.orchestration.graph.events import (
    emit_delta_events,
    extract_answer_presentation_metadata,
    make_done_event,
)
from backend.shared.error_protocol import sse_cancelled_event, sse_error_event
from backend.shared.logger import logger


def _emit_delta_events(*args, **kwargs):
    try:
        import backend.orchestration.graph.runner as runner_mod
        fn = getattr(runner_mod, "emit_delta_events", None)
        if callable(fn):
            return fn(*args, **kwargs)
    except Exception:
        pass
    from backend.orchestration.graph.events import emit_delta_events
    return emit_delta_events(*args, **kwargs)


class EventDispatcher:
    """消费队列事件并产出规范化的 SSE 事件流与终态。"""

    def __init__(
        self,
        merged_q: queue.Queue,
        ctx: dict,
        trace: Any,
        start_time: float,
        stop_event: threading.Event,
        memory: Any,
        product_session_id: str,
        session_id: str,
        question: str,
        user_id: str,
        tenant_id: str,
        product_id: str,
        idempotency_key: str,
        clarify_click: dict | None,
        fallback_deltas: bool,
        streamed: list[bool],
        persist_memory: bool,
        initial_state: dict,
    ) -> None:
        self.merged_q = merged_q
        self.ctx = ctx
        self.trace = trace
        self.start_time = start_time
        self.stop_event = stop_event
        self.memory = memory
        self.product_session_id = product_session_id
        self.session_id = session_id
        self.question = question
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.product_id = product_id
        self.idempotency_key = idempotency_key
        self.clarify_click = clarify_click
        self.fallback_deltas = fallback_deltas
        self.streamed = streamed
        self.persist_memory = persist_memory
        self.initial_state = initial_state

    def finalize_trace(
        self,
        status: str,
        metrics: dict | None = None,
        answer: str | None = None,
        error_code: str = "",
    ) -> None:
        """trace 幂等收尾：统一承载四条退出路径（每轮恰好 finish 一次）。"""
        if self.ctx.get("trace_finalized"):
            return
        self.ctx["trace_finalized"] = True
        try:
            if status == "cancelled":
                self.trace.tags["cancel_source"] = "user"
                stage = (
                    (self.ctx.get("cs_context_snapshot") or {}).get("cancel_stage")
                    or self.ctx.get("cancel_stage")
                    or "graph_boundary"
                )
                self.trace.tags["cancel_stage"] = str(stage)
                self.trace.metadata["cancelled"] = True
            elif status == "paused":
                self.trace.tags["pause_reason"] = "user_balance_insufficient"
                self.trace.metadata["paused"] = True
            if error_code:
                self.trace.tags["error_code"] = error_code
                self.trace.metadata["error_code"] = error_code
            _end_root(self.trace, status=status, metrics=metrics or {})
            trace_collector.finish(
                self.trace,
                answer if answer is not None else (self.ctx["final_answer"] or ""),
                int((time.time() - self.start_time) * 1000), "", "",
            )
            try:
                from backend.observability.question_ledger import (
                    record_question_from_trace,
                )
                from backend.shared.pii_mask import mask_pii

                _masked_q, _vault = mask_pii(
                    str(getattr(self.trace, "question", "") or ""),
                )
                record_question_from_trace(
                    self.trace, question=_masked_q,
                    answer_summary=str(self.ctx.get("final_answer") or ""),
                )
            except Exception:
                logger.debug("[GraphRunner] 问题台账写入失败", exc_info=True)
        except Exception:
            logger.debug("[GraphRunner] trace 收尾失败", exc_info=True)

    def dispatch(self) -> Generator[dict, None, None]:
        """执行事件消费主循环并生成终态。"""
        try:
            while True:
                kind, evt = self.merged_q.get()
                if kind == "done":
                    break
                yield evt

            # ── 收尾：检查用户中断 ──
            if self.stop_event is not None and self.stop_event.is_set():
                self.ctx["aborted"] = True
            if self.ctx["aborted"]:
                yield sse_cancelled_event()
                self.finalize_trace("cancelled", {"reason": "user_abort"})
                return

            # ── 收尾：用户余额不足暂停 ──
            if self.ctx["worker_paused"]:
                pause_message = (
                    "用户模型用量余额不足，本轮已暂停。补足余额后可以重新发送这条问题。"
                )
                yield {
                    "event": "paused",
                    "data": {
                        "code": "USER_BALANCE_INSUFFICIENT",
                        "message": pause_message,
                        "action": "top_up_and_resend",
                        "elapsed": max(0.0, time.time() - self.start_time),
                        "ts": time.time(),
                    },
                }
                self.finalize_trace(
                    "paused",
                    {"pause_reason": "user_balance_insufficient"},
                )
                return

            # ── 收尾：后台图执行报错 ──
            if self.ctx["worker_error"]:
                self.finalize_trace(
                    "error", {"error": "graph_failed"},
                    error_code=self.ctx.get("worker_error_code") or "",
                )
                return

            # ── 收尾：后台任务移交 ──
            if self.ctx.get("background_task"):
                task_id = self.ctx["background_task"]["task_id"]
                self.trace.tags["task_id"] = task_id
                self.trace.tags["execution_disposition"] = "background"
                self.finalize_trace("success", {"task_id": task_id})
                yield {"event": "done", "data": {
                    "answer": "已形成多步骤业务计划，已转为后台任务，可暂停后继续。",
                    "elapsed": max(0.0, time.time() - self.start_time),
                    "trace_id": self.trace.id, "task_id": task_id,
                    "sources": [],
                    "answer_source": "persistent_task", "reply_source": "system_notice",
                }}
                return

            # ── 收尾：最终文本提取与兜底汇总 ──
            answer = self.ctx["final_answer"]
            if not answer:
                answer = _fallback_summary_from_results(self.ctx["all_step_results"])
            if answer:
                self.ctx["final_answer"] = answer
                if not self.ctx["answer_source"]:
                    self.ctx["answer_source"] = "step_results"

            # ── 假打字机兜底 ──
            if self.fallback_deltas and not self.streamed[0] and answer:
                yield from _emit_delta_events(answer, None)
                if self.stop_event is not None and self.stop_event.is_set():
                    yield sse_cancelled_event()
                    self.finalize_trace("cancelled", {"reason": "user_abort"})
                    return

            # ── Tracing: 重建 span 树 ──
            state_for_trace = {
                "plan": self.ctx["current_plan"],
                "step_results": self.ctx["all_step_results"],
                "_supervisor_loop_count": _count_rounds_from_results(self.ctx["all_step_results"]),
                "_degraded_steps": set(),
                "_plan_changed": self.ctx["plan_changed"],
                "final_answer": answer,
                "route_mode": self.ctx["route_mode"],
                "cs_context": self.ctx["cs_context_snapshot"],
            }
            self.trace.tags["answer_source"] = self.ctx["answer_source"] or "unknown"
            self.trace.tags["delta_source"] = (
                "llm_stream" if self.streamed[0] else "runner_fallback"
            )
            trace_from_state(self.trace, state_for_trace)
            self.finalize_trace(
                "success", {"span_count": len(self.trace.spans) - 1}, answer=answer,
            )

            # ── 客服轮次持久化 ──
            _persist_cs_turn_if_needed(
                self.ctx["cs_context_snapshot"], self.session_id, self.question, answer,
                self.trace.id,
                entry_order_no=self.ctx.get("cs_entry_order_no") or "",
                tenant_id=self.tenant_id,
            )

            # ── 记忆持久化 ──
            if (
                answer and not self.ctx["memory_persisted"]
                and not self.ctx["cs_context_snapshot"].get("conversation_id")
                and self.persist_memory
            ):
                self.ctx["memory_persisted"] = True
                try:
                    memory_kwargs = {
                        "user_id": self.user_id,
                        "tenant_id": self.tenant_id,
                        "product": self.product_id,
                        "trace_id": str(self.trace.id or ""),
                    }
                    answer_metadata = extract_answer_presentation_metadata(
                        answer, self.ctx["all_step_results"],
                    )
                    if answer_metadata:
                        memory_kwargs["answer_metadata"] = answer_metadata
                    if self.idempotency_key:
                        memory_kwargs["idempotency_key"] = self.idempotency_key
                    self.memory.end_turn(
                        self.product_session_id, self.question, answer,
                        **memory_kwargs,
                    )
                except Exception:
                    logger.warning("[GraphRunner] Memory end_turn 写入失败", exc_info=True)

            # 内部事件：ask() 从这里取最终回答
            yield {"event": _ANSWER_EVENT, "data": {"answer": answer}}
            _funnel_note_resolved(
                self.clarify_click, answer, session_id=self.product_session_id,
                trace_id=str(getattr(self.trace, "id", "") or ""),
            )

            # ── 上下文用量快照 ──
            context_usage = None
            try:
                from backend.context_budget import context_budget as _cb
                _extra = [
                    str(sr.get("output"))
                    for sr in self.ctx["all_step_results"].values()
                    if isinstance(sr, dict) and sr.get("output") is not None
                ]
                context_usage = _cb.calculate_usage(
                    messages=self.initial_state.get("messages"),
                    extra_texts=_extra,
                ).to_dict()
            except Exception:
                logger.debug("context_usage 计算失败，done 不携带", exc_info=True)

            yield make_done_event(
                answer, self.ctx["all_step_results"], self.start_time,
                usage=self.ctx["usage"],
                pending_action=self.ctx.get("cs_pending_action"),
                trace_id=self.trace.id,
                context_usage=context_usage,
                include_pending_action="cs_pending_action" in self.ctx,
                answer_source=self.ctx.get("answer_source") or "unknown",
            )
        except GeneratorExit:
            reason = (
                "user_abort"
                if (self.stop_event is not None and self.stop_event.is_set())
                or self.ctx["aborted"] else "client_disconnect"
            )
            if self.stop_event is not None:
                self.stop_event.set()
            self.finalize_trace(
                "cancelled" if reason == "user_abort" else "error",
                {"reason": reason},
            )
            raise
        except RequestCancelled as e:
            logger.info(f"[GraphRunner] 生成器路径被用户中止: stage={e.stage}")
            yield sse_cancelled_event()
            self.finalize_trace("cancelled", {"reason": "user_abort"})
        except (asyncio.TimeoutError, TimeoutError) as e:
            logger.error(f"[GraphRunner] 生成器路径执行超时: {e}", exc_info=True)
            yield sse_error_event(e)
            self.finalize_trace("error", {"error": "timeout"}, error_code="TIMEOUT")
        except Exception as e:
            logger.error(f"[GraphRunner] 流式执行失败: {e}", exc_info=True)
            event, error_code = _runner_error_event(e)
            yield event
            self.finalize_trace(
                "error", {"error": error_code.lower()}, error_code=error_code,
            )
