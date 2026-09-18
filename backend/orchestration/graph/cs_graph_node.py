"""
orchestration/graph/cs_graph_node.py — Main Graph ↔ CS Graph 适配器

职责 (仅 State 转换，不含业务逻辑):
  1. 从 OrchestratorState 构建 CS Graph 输入 (new_cs_graph_input)
  2. 调用 get_cs_graph().invoke(cs_input)
  3. 通过 build_cs_graph_result() 将 CSGraphState → CSGraphResult
  4. 将 CSGraphResult 映射回 Main State 字段

设计参考: docs/customer-service/langgraph-multi-expert-design.md §7.3
"""
from __future__ import annotations

import re

from backend.customer_service.context import merge_cs_context
from backend.customer_service.graph_builder import get_cs_graph
from backend.customer_service.graph_state import new_cs_graph_input
from backend.customer_service.models.graph_result import build_cs_graph_result
from backend.shared.logger import logger

_FALLBACK_ANSWER = "抱歉，客服系统暂时不可用，请稍后再试。"


def cs_graph_node(state: dict) -> dict:
    """Main Graph → CS Graph → Main Graph 适配器

    输入: OrchestratorState (Main Graph 状态)
    输出: dict — 写回 Main State 的字段子集
    """
    cs_context = state.get("cs_context", {})
    conversation_id = cs_context.get("conversation_id", "")

    cs_input = new_cs_graph_input(
        user_message=state.get("question", ""),
        user_id=cs_context.get("authenticated_user_id", ""),
        session_id=cs_context.get("session_id", ""),
        conversation_id=conversation_id,
        cs_route=cs_context.get("cs_route", {}),
    )

    try:
        graph = get_cs_graph()
        invoke_config = _build_invoke_config(conversation_id)
        final_state = graph.invoke(cs_input, config=invoke_config)
        result = build_cs_graph_result(final_state)
    except Exception:
        logger.exception("[cs_graph_node] CS Graph 执行异常，降级返回兜底回复")
        return _fallback_update(state)

    _stamp_execution_tags(final_state)
    _persist_audit_records(final_state)
    update = _build_main_state_update(state, result)
    # L2 拒答兜底追问（2026-09-19）：知识域拒答时在原始输出附带 _clarify
    # （events.py 据此发 clarification 事件），拒答正文照常返回
    clarify = _refusal_clarify(final_state, state)
    if clarify is not None:
        update["_clarify"] = clarify
    return update


def _refusal_clarify(final_state: dict, main_state: dict) -> dict | None:
    """CS 知识域拒答判定（结构化条件，不做文本匹配）。

    命中条件 = cs_reporter._summarize_expert_results 落「暂时无法找到相关
    信息」兜底的结构化前提：正常作答路径（非 handoff/pending）、路由到
    KNOWLEDGE 域、专家无 response_draft/data、非失败态。
    """
    try:
        from backend.config import REFUSAL_CLARIFY_ENABLED
        from backend.orchestration.graph.clarify_content import (
            build_refusal_clarify,
            clarify_allowed,
            mark_clarified,
        )

        if not REFUSAL_CLARIFY_ENABLED:
            return None
        decision = final_state.get("supervisor_decision") or {}
        if decision.get("next_action") in ("handoff", "pending"):
            return None
        cs_route = final_state.get("cs_route") or {}
        if cs_route.get("domain") != "KNOWLEDGE":
            return None
        expert = final_state.get("last_expert_result") or {}
        if (expert.get("response_draft") or expert.get("data")
                or expert.get("action_result")
                or expert.get("status") == "failed" or expert.get("error")):
            return None
        session_id = main_state.get("session_id", "")
        if not clarify_allowed(session_id):
            return None
        mark_clarified(session_id)
        return build_refusal_clarify(
            main_state.get("question", ""), "customer_service")
    except Exception as e:
        logger.warning(f"[cs_graph_node] 拒答追问判定失败，输出原回复: {e}")
        return None


def _persist_audit_records(final_state: dict) -> None:
    """把本 turn 的审计条目与业务动作落库（P1，audit-report §P0-7）。

    audit_logs / agent_actions 表此前零写入 —— 审计只活在 graph state。
    写入失败：error 级日志 + 指标，不阻断 chat 主流程（关键旁路语义），
    绝不静默吞掉。
    """
    audit_entries = [
        e for e in (final_state.get("cs_audit_entries") or []) if isinstance(e, dict)
    ]
    action_result = final_state.get("cs_action_result") or {}
    action_record = (
        action_result.get("action_record")
        if isinstance(action_result, dict)
        else None
    )

    if not audit_entries and not action_record:
        return

    def _write() -> None:
        from backend.customer_service._db_loop import run_sync
        run_sync(_async_persist_audit(audit_entries, action_record))

    try:
        _write()
    except Exception as exc:
        logger.error(
            "[cs_graph_node] 审计落库失败（audit_entries=%d, action_record=%s）: %s",
            len(audit_entries), bool(action_record), exc, exc_info=True,
        )
        try:
            from backend.observability.metrics import record_cs_store_db_failure
            record_cs_store_db_failure("audit", "persist")
        except Exception:
            logger.debug("[cs_graph_node] metrics unavailable")


async def _async_persist_audit(audit_entries: list, action_record: dict | None) -> None:
    from backend.customer_service.repository import AuditRepository
    from backend.memory.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        repo = AuditRepository(db)
        for entry in audit_entries:
            await repo.insert_audit_log(entry)
        if action_record and isinstance(action_record, dict):
            # AgentActionRecord.to_dict() 不含 conversation_id/user_id ——
            # 从审计条目/动作记录补齐（动作执行必有对应审计条目）
            first = audit_entries[0] if audit_entries else {}
            await repo.insert_agent_action({
                **action_record,
                "conversation_id": first.get("conversation_id", ""),
                "user_id": first.get("user_id", ""),
                "confirmation_state": "success",
            })
        await db.commit()


def _stamp_execution_tags(final_state: dict) -> None:
    """把 CS Graph 内部执行结果写进 trace tags（路由一致率/转人工率数据源）。

    - cs_expert_final: supervisor 最终派发的 expert（与 tags.cs_target 对照）
    - cs_expert_visited: 会话中访问过的全部 expert（路由翻转分析）
    - cs_handoff_state: 最终 handoff 状态（转人工率）
    """
    try:
        from backend.observability.tracer import trace_collector
        trace = trace_collector.current()
        if trace is None:
            return
        visited = [
            (e.get("node") if isinstance(e, dict) else str(e))
            for e in (final_state.get("expert_history") or [])
        ]
        final_expert = final_state.get("current_expert") or (
            visited[-1] if visited else ""
        )
        if final_expert:
            trace.tags["cs_expert_final"] = final_expert
        if visited:
            trace.tags["cs_expert_visited"] = ",".join(dict.fromkeys(visited))
        handoff = final_state.get("handoff_state") or ""
        if handoff:
            trace.tags["cs_handoff_state"] = handoff
            # 转人工 trigger 序列 → metadata（转人工率三桶拆分的数据源；
            # audit entries 本身不进 trace record，故在此提取）
            triggers = []
            for entry in final_state.get("cs_audit_entries") or []:
                detail = (entry or {}).get("detail", "") if isinstance(entry, dict) else ""
                m = re.search(r"trigger=([a-z_]+)", detail or "")
                if m:
                    triggers.append(m.group(1))
            if triggers:
                trace.metadata["cs_handoff_triggers"] = triggers
    except Exception:
        logger.debug("[cs_graph_node] 执行标签写入失败", exc_info=True)


def _build_main_state_update(original_state: dict, result: dict) -> dict:
    """将 CSGraphResult 映射为 Main State 字段更新

    cs_context 合并策略:
    - 保留: authenticated_user_id, session_id, cs_target (来自原始 state)
    - 覆盖: conversation_id, handoff_state, confirmation_state (来自 CS Graph)
    """
    original_ctx = original_state.get("cs_context", {})

    return {
        "final_answer": result.get("final_answer", _FALLBACK_ANSWER),
        "cs_context": merge_cs_context(original_ctx, result),
        "cs_action_result": result.get("action_result") or {},
        "cs_audit_entries": result.get("audit_entries", []),
        # P3.1：等待确认时透传 pending_action（runner 快照 → SSE done 帧
        # → 前端 CSConfirmCard）；非 pending 态为 None
        "cs_pending_action": result.get("pending_action") or None,
    }


def _fallback_update(state: dict) -> dict:
    """CS Graph 异常时的安全网 — 不崩溃 Main Graph"""
    original_ctx = state.get("cs_context", {})
    return {
        "final_answer": _FALLBACK_ANSWER,
        "cs_context": {
            "authenticated_user_id": original_ctx.get("authenticated_user_id"),
            "session_id": original_ctx.get("session_id"),
            "cs_target": original_ctx.get("cs_target"),
        },
        "cs_action_result": {},
        "cs_audit_entries": [],
        "cs_pending_action": None,
    }


def _build_invoke_config(conversation_id: str) -> dict:
    """构建 CS Graph invoke config。

    Phase 5: 当 checkpointer 启用时，thread_id 用于多轮对话状态持久化。
    """
    from backend.config.customer_service import CS_GRAPH_RECURSION_LIMIT

    config: dict = {
        "recursion_limit": CS_GRAPH_RECURSION_LIMIT,
    }
    if conversation_id:
        config["configurable"] = {"thread_id": conversation_id}
    return config
