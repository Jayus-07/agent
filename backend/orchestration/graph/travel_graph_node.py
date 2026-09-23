"""orchestration/graph/travel_graph_node.py — Main Graph ↔ 旅游域图 适配器

与 cs_graph_node 同一职责，只做状态转换，不含业务逻辑：
  1. OrchestratorState → 旅游域图输入（new_travel_graph_input）
  2. 调用 get_travel_graph().invoke(...)
  3. TravelGraphResult → Main State 字段映射
异常一律降级为兜底回复，绝不把主图带崩 —— 与客服域同一条安全带。
"""
from __future__ import annotations

from backend.shared.logger import logger
from backend.travel.graph_builder import get_travel_graph
from backend.travel.graph_state import new_travel_graph_input
from backend.travel.models.graph_result import build_travel_graph_result

_FALLBACK_ANSWER = "抱歉，旅游规划服务暂时不可用，请稍后再试。"


def _maybe_cancel_active_run(state: dict, conversation_id: str) -> dict | None:
    """「取消整个规划」短路（STOP G3，任务书 §16/§17）。

    仅当会话存在活跃 travel run（run_id 非空且非 completed/cancelled）
    且消息命中保守 cancel 词表（is_cancel_run_query：「不去海游馆了」类
    局部排除句不触发）时执行：CANCEL_TRAVEL_RUN 原子 mutation（run CAS，
    seq 保留 → 下一 run 不撞号；pending 清；摘要槽位保留）——不跑专家图。
    保守失败：无 run / 词表未命中 / CAS stale 一律返回 None 放行正常流程。
    """
    question = state.get("question") or state.get("query") or ""
    try:
        from backend.travel.slot_filler import is_cancel_run_query
        if not is_cancel_run_query(question):
            return None
        from backend.orchestration.context.context_repository import (
            ContextMutation,
            MutationType,
            get_conversation_context_repository,
        )

        repo = get_conversation_context_repository()
        snap = repo.peek(state.get("tenant_id") or "",
                         state.get("user_id") or "", conversation_id)
        if (snap is None or not snap.travel_run_id
                or snap.travel_stage in ("completed", "cancelled")):
            return None
        run_id = snap.travel_run_id
        result = repo.mutate(
            state.get("tenant_id") or "", state.get("user_id") or "",
            conversation_id,
            ContextMutation(MutationType.CANCEL_TRAVEL_RUN,
                            {"expected_run_id": run_id}))
        if result.status != "applied":
            logger.info("[travel.run] cancel 未生效(status=%s)，放行正常流程",
                        result.status)
            return None
        logger.info("[travel.run] event=travel.run.cancelled run=%s", run_id)
        try:  # trace 观测（软失败）
            from backend.observability.tracer import trace_collector
            trace = trace_collector.current()
            if trace is not None:
                trace.tags["travel_status"] = "cancelled"
                trace.tags["travel_resume_mode"] = "cancel"
                trace.tags["travel_run_id"] = run_id
        except Exception:
            pass
        original = state.get("travel_context") or {}
        return {
            "final_answer": (
                f"好的，已取消当前的行程规划（{run_id}）。"
                "想重新规划随时告诉我。"),
            "travel_context": {
                "conversation_id": conversation_id,
                "travel_route": {
                    **(original.get("travel_route") or {}),
                    "source": "pending_resume",
                    "resume_mode": "cancel",
                    "cancelled_run": run_id,
                },
            },
        }
    except Exception:  # noqa: BLE001 — cancel 判定失败绝不阻断正常规划
        logger.debug("[travel_graph_node] cancel 判定失败，放行正常流程",
                     exc_info=True)
        return None


def travel_graph_node(state: dict) -> dict:
    """Main Graph → 旅游域图 → Main Graph 适配器"""
    travel_context = state.get("travel_context") or {}
    session_id = state.get("session_id", "")
    conversation_id = travel_context.get("conversation_id") or session_id
    travel_route = travel_context.get("travel_route") or {}

    # 取消整个规划短路（STOP G3）：命中即在上下文层完成 CANCEL，
    # 不进域图（不消耗专家/校验轮次）。
    cancel_update = _maybe_cancel_active_run(state, conversation_id)
    if cancel_update is not None:
        return cancel_update

    graph_input = new_travel_graph_input(
        user_message=state.get("question") or state.get("query") or "",
        user_id=state.get("user_id", ""),
        session_id=session_id,
        conversation_id=conversation_id,
        travel_route=travel_route,
    )

    try:
        graph = get_travel_graph()
        config = _build_invoke_config(conversation_id)
        # ── resume 模式判定（STOP F3）────────────────────────
        # checkpoint 有值 → checkpoint（自然续跑）；thread 无 checkpoint
        # 但会话有 travel 摘要 → reconstruct（从 ConversationContext 重建
        # brief 基底，graceful reconstruction，绝不 500）；两者皆无 → fresh。
        resume_mode, extra_input = _detect_resume_mode(
            graph, config, state, conversation_id)
        if extra_input:
            graph_input.update(extra_input)
        # resume 通道：上层在 travel_context.resume_decision 带回用户决策时，
        # 用 Command(resume=...) 恢复被 interrupt 暂停的域图（thread_id 必须与
        # 中断轮一致——checkpointer 按 thread 定位暂停态）。
        resume_decision = travel_context.get("resume_decision")
        if resume_decision:
            from langgraph.types import Command
            final_state = graph.invoke(Command(resume=resume_decision), config=config)
        else:
            final_state = graph.invoke(graph_input, config=config)
        result = build_travel_graph_result(final_state)
    except Exception:
        logger.exception("[travel_graph_node] 旅游域图执行异常，降级返回兜底回复")
        return _fallback_update(state)

    # interrupt 透传（任务书 §13，Phase 6）：域图暂停等决策时，把待决项
    # 结构化放 travel_context.pending_decision，final_answer 呈现请决定文案。
    if isinstance(final_state, dict) and final_state.get("__interrupt__"):
        return _interrupt_update(state, final_state)

    # run 同步在前：tags 需要 run_id（任务书 §21 观测字段）
    run_id = _sync_travel_run(state, final_state, result, travel_route)
    _stamp_execution_tags(final_state, result, resume_mode=resume_mode,
                          run_id=run_id)
    return _build_main_state_update(result)


def _detect_resume_mode(
    graph, config: dict, state: dict, conversation_id: str,
) -> tuple[str, dict]:
    """checkpoint 探测与 graceful reconstruction 判定（STOP F3）。

    Returns:
        (resume_mode, extra_input)：
        - ("checkpoint", {})            thread 有持久化状态，自然续跑
        - ("reconstruct", {reconstruct_brief})  checkpoint 丢失/不存在，
          但会话有 travel 摘要 → 从 ConversationContext 重建 brief 基底
          （slot_filler 以其为 previous 合并本轮消息，不 500）
        - ("fresh", {})                 首次规划 / checkpointer 未启用
    """
    try:
        snap = graph.get_state(config)
        if snap is not None and getattr(snap, "values", None):
            return "checkpoint", {}
    except Exception:
        # checkpointer 未启用时 get_state 抛错——与「有 checkpointer 但
        # thread 无数据」同样落到下方摘要探测（摘要存在仍可 reconstruct）
        logger.debug("[travel_graph_node] checkpoint 探测不可用")

    # thread 无 checkpoint：会话摘要可重建则 reconstruct，否则首轮 fresh
    try:
        from backend.orchestration.context.context_repository import (
            get_conversation_context_repository,
        )

        ctx = get_conversation_context_repository().peek(
            state.get("tenant_id") or "", state.get("user_id") or "",
            conversation_id)
        if ctx is None or not (ctx.destination or ctx.days
                               or ctx.travel_run_id):
            return "fresh", {}
        reconstruct_brief = {
            key: getattr(ctx, key)
            for key in ("destination", "origin", "start_date", "days",
                        "party_size", "budget_cny", "lodging", "preferences",
                        "must_go", "avoid")
            if getattr(ctx, key)
        }
        logger.warning(
            "[travel_graph_node] checkpoint 缺失，从会话上下文重建"
            "（resume_mode=reconstruct, run=%s）", ctx.travel_run_id)
        return "reconstruct", {"reconstruct_brief": reconstruct_brief}
    except Exception:
        logger.debug("[travel_graph_node] 会话摘要读取失败，按 fresh 处理",
                     exc_info=True)
        return "fresh", {}


def _sync_travel_run(state: dict, final_state: dict, result: dict,
                     travel_route: dict) -> str:
    """执行后的 TravelRun 结构化同步（STOP F1/F2，软失败）。

    run 身份（首次/NEW_RUN 时换）、阶段标记与结构化 pending 写进
    ConversationContext——下一轮路由层 TravelPendingResolver 的数据基础。
    成功出单（status=success）追加 completed 收尾 + 清 pending（T15）。

    Returns:
        本次同步后的 run_id（同步失败/不可同步时空串）。
    """
    try:
        from backend.orchestration.context.conversation_context import (
            mark_travel_run_completed,
            sync_travel_run_to_context,
        )

        conversation_id = (
            (state.get("travel_context") or {}).get("conversation_id")
            or state.get("session_id") or "")
        # NEW_RUN 两个来源（STOP F2）：resolver 在 pending 场景打的
        # travel_route 标记，以及无 pending 时对消息的直接判定（travel 域
        # 单一事实源 is_new_run_query——任务齐备出单后再说「重新规划」
        # 也必须换 run，不能只在有 pending 时生效）。
        from backend.travel.slot_filler import is_new_run_query

        new_run = (bool((travel_route or {}).get("new_run"))
                   or is_new_run_query(state.get("question") or ""))
        run_id = sync_travel_run_to_context(
            state.get("tenant_id") or "",
            state.get("user_id") or "",
            conversation_id,
            brief=final_state.get("brief") or {},
            missing_slots=final_state.get("brief_missing") or [],
            new_run=new_run,
        ) or ""
        if result.get("status") == "success":
            mark_travel_run_completed(
                state.get("tenant_id") or "",
                state.get("user_id") or "", conversation_id)
        if run_id:
            logger.info(
                "[travel.run] event=travel.run.turned run=%s status=%s "
                "resume_mode=%s", run_id, result.get("status", ""),
                (travel_route or {}).get("resume_mode") or "fresh")
        return run_id
    except Exception:  # noqa: BLE001 — 同步失败绝不影响主链
        logger.debug("[travel_graph_node] run 同步失败", exc_info=True)
        return ""


def _interrupt_update(state: dict, final_state: dict) -> dict:
    """域图 interrupt 暂停 → 主图状态呈现待决项（结构化 + 可读文案）。"""
    try:
        interrupts = final_state.get("__interrupt__") or []
        payload = {}
        for it in interrupts:
            value = getattr(it, "value", None)
            if isinstance(value, dict) and value.get("items"):
                payload = value
                break
        lines = ["行程已生成，但有几项需要你决定（回复「保留」或「移除：地点名」）："]
        for item in payload.get("items", []):
            lines.append(f"- {item.get('message', '')}")
        for opt, desc in (payload.get("options") or {}).items():
            lines.append(f"· {opt}: {desc}")
        original = state.get("travel_context") or {}
        return {
            "final_answer": "\n".join(lines),
            "travel_context": {
                "conversation_id": original.get("conversation_id", ""),
                "travel_route": original.get("travel_route", {}),
                "pending_decision": payload,
            },
        }
    except Exception:
        logger.exception("[travel_graph_node] interrupt 透传失败，降级兜底")
        return _fallback_update(state)


def _build_main_state_update(result: dict) -> dict:
    """TravelGraphResult → Main State 字段更新。

    只写两个字段：final_answer 与 travel_context。旅游域大对象（itinerary）
    放在 travel_context 里，避免污染主状态、也避免 checkpointer 把整份行程
    反复序列化。
    """
    return {
        "final_answer": result.get("final_answer") or _FALLBACK_ANSWER,
        "travel_context": result.get("travel_context") or {},
    }


def _fallback_update(state: dict) -> dict:
    original = state.get("travel_context") or {}
    return {
        "final_answer": _FALLBACK_ANSWER,
        "travel_context": {
            "conversation_id": original.get("conversation_id", ""),
            "travel_route": original.get("travel_route", {}),
        },
    }


def _stamp_execution_tags(final_state: dict, result: dict,
                          resume_mode: str = "", run_id: str = "") -> None:
    """把执行结果写进 trace tags —— 旅游域的质量指标数据源。

    status / 校验码 / 修复轮数 / 置信度都打标，后续做「约束违反率」
    「一次通过率」「平均修复轮数」时不需要再回头改埋点。埋点软失败。
    """
    try:
        from backend.observability.tracer import trace_collector
        trace = trace_collector.current()
        if trace is None:
            return
        trace.tags["travel_status"] = result.get("status", "")
        if resume_mode:
            # 任务书 §21：resume 模式必须可观测（checkpoint / reconstruct /
            # fresh / new_run / continue）
            trace.tags["travel_resume_mode"] = resume_mode
        if run_id:
            trace.tags["travel_run_id"] = run_id
        missing = final_state.get("brief_missing") or []
        if missing:
            trace.tags["travel_pending_slots"] = ",".join(missing)
        dirty = final_state.get("brief_changed_fields") or []
        if dirty:
            trace.tags["travel_dirty_fields"] = ",".join(dirty[:8])
        brief = final_state.get("brief") or {}
        if brief.get("destination"):
            trace.tags["travel_destination"] = brief["destination"]

        validation = final_state.get("validation") or {}
        codes = [v.get("code", "") for v in validation.get("violations", [])]
        trace.metadata["travel_validation"] = {
            "codes": list(dict.fromkeys(c for c in codes if c)),
            "errors": sum(1 for v in validation.get("violations", [])
                          if v.get("level") == "error"),
            "warnings": sum(1 for v in validation.get("violations", [])
                            if v.get("level") == "warning"),
            "decision_required": sum(
                1 for v in validation.get("violations", [])
                if v.get("level") == "decision_required"),
            "repair_rounds": final_state.get("repair_rounds", 0),
            "steps": final_state.get("step_count", 0),
        }
        itinerary = final_state.get("itinerary") or {}
        if itinerary:
            trace.metadata["travel_confidence"] = itinerary.get("confidence")
        # 持久化状态（任务书 §10，Phase 4）：降级时间段在 trace 可见，
        # 「跨轮改单失效」类用户反馈可直接对齐当时的服务端状态。
        if final_state.get("persistence_status"):
            trace.metadata["travel_persistence"] = final_state["persistence_status"]
    except Exception:
        logger.debug("[travel_graph_node] 执行标签写入失败", exc_info=True)


def _build_invoke_config(conversation_id: str) -> dict:
    """构建域图 invoke config。

    thread_id 始终给出：checkpointer 开启时 LangGraph 强制要求，缺失会直接
    抛错。无会话标识时用一次性 id，避免不同请求共享同一份 checkpoint
    （共享比报错更危险 —— 用户会看到别人的行程）。
    """
    from uuid import uuid4

    from backend.config.travel import TRAVEL_GRAPH_RECURSION_LIMIT

    return {
        "recursion_limit": TRAVEL_GRAPH_RECURSION_LIMIT,
        "configurable": {
            # STOP F3：`travel:` namespace 前缀。域图 checkpoint 表与主图/
            # 客服域共用（PostgresSaver 同库），而 CS 域图用裸 conversation_id
            # 做 thread_id——不加前缀，同一会话「先客服后旅游」会互相覆盖
            # checkpoint 状态。前缀 = namespace 隔离（checkpointer 默认关，
            # 无存量迁移问题）。
            "thread_id": (f"travel:{conversation_id}" if conversation_id
                          else f"travel-{uuid4().hex}"),
        },
    }
