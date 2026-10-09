"""runner.py — 图执行统一核心（GraphRunner）

ask() 与 stream_events() 的单一实现（重构 #2：收敛双轨编排）。

历史问题: MultiAgentSystem.ask()（同步）与 stream_events()（SSE）各自维护
一套「执行图 → 捕获 state → 重建 trace → 持久化」逻辑，语义差异（错误路径
是否 finish trace、final_answer 何时空）需要人肉对齐，改任何一边都要同步另一边。

现在: GraphRunner.iter_events() 产出统一事件流
    status / log / delta / _answer(内部) / done / error
  - stream_events()   → 逐个转发给 SSE（过滤 _answer 内部事件）
  - ask()             → 物化事件流，取 _answer 作为最终回答

执行语义要点（与原两轨对齐后的统一口径）:
  - Guard 拦截/澄清 → 短路话术，不进图
  - 用户中止 → error 事件，不产出 done，不落库
  - 图执行异常 → error 事件，trace 以 error 收尾，不产出 done
  - final_answer 为空（正常完成）→ 用 step_results 兜底汇总（两轨统一，
    旧 stream 路径空回答无提示属体验缺陷）
  - 仅 final_answer 非空才 memory.end_turn（避免历史出现空气泡/错误气泡）
"""
from __future__ import annotations

import queue
import threading
import time
import uuid
from functools import wraps
from typing import Generator

from backend.config import ENABLE_TOKEN_STREAMING, MAIN_GRAPH_RECURSION_LIMIT
from backend.context_budget.errors import ContextBudgetExceededError
from backend.infra.llm.proxy import reset_stream_sink
from backend.orchestration.graph.builder import _parse_event
from backend.orchestration.graph.events import (
    emit_delta_events,
    make_done_event,
    make_initial_state,
    make_step_log_event,
    make_step_payload,
    make_todo_event,
    make_usage_event,
    stream_node_events,
    summarize_turn_usage,
)
from backend.orchestration.request_context import RequestContext, put_context
from backend.security.input_guard import (
    GuardAction,
    GuardCategory,
    RiskLevel,
    get_input_guard,
)
from backend.shared.logger import logger

# _answer 是 runner → 调用方的内部事件（携带最终回答文本），
# 不属于 SSE 协议，stream_events 转发层必须过滤
_ANSWER_EVENT = "_answer"


def _pin_prompt_snapshot(iter_fn):
    """在图生成器整个生命周期内固定 Prompt 模板。"""
    @wraps(iter_fn)
    def wrapper(self, *args, **kwargs):
        from backend.prompts.hot_reload import ensure_prompt_snapshot_fresh
        from backend.prompts.service import prompt_service

        try:
            ensure_prompt_snapshot_fresh()
        except Exception:  # noqa: BLE001 - 热更新旁路不得阻断请求
            logger.debug("[GraphRunner] Prompt Runtime freshness check skipped", exc_info=True)
        with prompt_service.pin_snapshot():
            yield from iter_fn(self, *args, **kwargs)

    return wrapper


def _resolve_followup_for_request(
    *,
    trace,
    raw_question: str,
    tenant_id: str,
    user_id: str,
    session_id: str,
    l1_messages: list,
) -> str | None:
    """P2.7：Router 之前的统一 follow-up 解析（全局入口专用）。

    流程：load ConversationContext → FollowUpResolver → standalone_query。
    返回值语义：
      - 非 None 字符串：本轮实际进 Router 的 query（解析结果或原文回退）
      - None：need_clarification，调用方短路输出澄清（P2.9）

    全程软失败：任何异常都返回原文，绝不阻断主聊天链。
    Trace 只记槽位名与 query，不记完整上下文（P2.11 防敏感泄漏）。
    """
    try:
        from langchain_core.messages import HumanMessage

        from backend.orchestration.context.conversation_context import (
            ConversationContext,
        )
        from backend.orchestration.context.context_repository import (
            ContextMutation,
            MutationType,
            get_conversation_context_repository,
        )
        from backend.orchestration.context.follow_up_resolver import (
            resolve_followup,
        )

        last_user_turn = ""
        for msg in reversed(l1_messages or []):
            if isinstance(msg, HumanMessage):
                last_user_turn = (msg.content or "").strip()
                break

        repo = get_conversation_context_repository()
        ctx = repo.get(tenant_id, user_id, session_id)
        if ctx is None:
            ctx = ConversationContext(tenant_id=tenant_id or "",
                                      user_id=user_id or "",
                                      conversation_id=session_id or "")
        resolution = resolve_followup(raw_question, ctx, last_user_turn)

        # P2.11 Trace：span attributes + trace tags（只记低敏字段）
        try:
            trace.tags["follow_up_detected"] = str(
                resolution["follow_up_detected"]).lower()
            trace.tags["rewrite_method"] = resolution["rewrite_method"]
            trace.tags["context_slots_used"] = ",".join(
                resolution["used_context"][:5])
            if resolution["need_clarification"]:
                trace.tags["need_clarification"] = "true"
        except Exception:
            pass

        # 状态回写走原子 mutation（STOP G：与 apply_resolution_to_context
        # 语义对齐——overwrite 优先，否则 resolved 时记 topic）
        write_payload: dict = {}
        if resolution.get("overwrite_destination"):
            write_payload["overwrite_destination"] = resolution[
                "overwrite_destination"]
        elif resolution.get("follow_up_detected") and resolution.get("resolved"):
            write_payload = {"resolved": True,
                             "topic": (resolution.get("standalone_query") or "")[:40]}
        if write_payload:
            repo.mutate(tenant_id, user_id, session_id, ContextMutation(
                MutationType.APPLY_FOLLOWUP_RESOLUTION, write_payload))

        if resolution["need_clarification"]:
            logger.info(
                "[Runner] follow-up 需要澄清: raw=%s used=%s",
                resolution["raw_query"][:40], resolution["used_context"])
            return None

        standalone = resolution["standalone_query"] or raw_question
        if standalone != raw_question:
            logger.info(
                "[Runner] follow-up 解析: %r → %r (method=%s, used=%s)",
                raw_question[:40], standalone[:60],
                resolution["rewrite_method"], resolution["used_context"])
        return standalone
    except Exception as exc:  # noqa: BLE001 — 解析失败回退原文
        logger.warning("[Runner] follow-up 解析失败，回退原文: %s", exc)
        return raw_question


def _should_bypass_guard_for_human_relay(
    guard_result,
    *,
    domain_hint: str,
    user_id: str,
    session_id: str,
) -> bool:
    """判断人工接管期是否可以绕过低风险体验型澄清。

    人工会话中的“1”“123”等短消息仍需落客服消息库，不能被全局
    InputGuard 在客服预过滤之前短路；安全相关澄清不在豁免范围内。
    """
    if domain_hint != "customer_service":
        return False
    if guard_result.action != GuardAction.CLARIFY:
        return False
    if guard_result.risk_level != RiskLevel.LOW:
        return False
    if guard_result.category not in {
        GuardCategory.GARBAGE,
        GuardCategory.AMBIGUOUS,
    }:
        return False

    try:
        from backend.orchestration.graph.cs_prefilter import (
            _active_relay_state,
        )

        return _active_relay_state(user_id, session_id) is not None
    except Exception:
        logger.debug(
            "[GraphRunner] human relay state lookup failed",
            exc_info=True,
        )
        return False


def _is_explicit_handoff(question: str) -> bool:
    """是否为显式转人工表述（复用 handoff 关键词/正则表，零模型调用）。

    供 input_guard clarify 豁免判断；检测器不可用时返回 False（保持
    原短路行为，宁可多澄清一次也不误放行）。
    """
    try:
        from backend.customer_service.handoff import detect_handoff_trigger
        return detect_handoff_trigger(question) is not None
    except Exception:  # noqa: BLE001 — 检测失败按非转人工处理
        return False


def _is_confirmation_text(question: str) -> bool:
    """是否为确认/取消类短词（P3.5 豁免，同转人工豁免模式）。

    「确认/取消/是的/不了」等确认语义短词会被 guard 的模糊问题→clarify
    规则拦在图外，CS pending_handler 永远收不到（剧本 C 文本取消路径
    实测失效）。检测器不可用时返回 False 保持短路行为。
    """
    try:
        from backend.customer_service.confirmation import detect_confirmation_intent
        from backend.customer_service.confirmation import ConfirmationIntent
        return detect_confirmation_intent(question or "") != ConfirmationIntent.NONE
    except Exception:  # noqa: BLE001
        return False


def _domain_node_names() -> set[str]:
    """已注册域图的主图节点名集合（cs_graph_node / travel_graph_node / …）。

    runner 的输出消费逻辑按节点名白名单取 final_answer，域图节点必须
    在内，否则域图跑完的答案会被主图丢弃。用注册表而非硬编码：新增域
    图（domains/<x>/register.py）注册即生效，无需改 runner。
    """
    try:
        from backend.orchestration.domain_registry import domain_graph_registry
        return domain_graph_registry.get_node_names()
    except Exception:  # noqa: BLE001 — 注册表不可用时退回仅内置节点
        logger.debug("[GraphRunner] domain registry 不可用，按空域图节点集处理", exc_info=True)
        return set()


def _looks_like_refusal_text(message: str) -> bool:
    """回答是否为拒答/短路类文案（追问漏斗 resolved 的近似口径）。

    只用于漏斗计数，不是门禁：宁可漏计 resolved 也不误计。覆盖 reporter
    拒答模板、追问短文案、guard/域门禁的「无法处理」类话术。
    """
    from backend.orchestration.graph.clarify_content import CLARIFY_STANDALONE_TEXT

    text = (message or "").lstrip()
    if not text:
        return True
    return (
        text.startswith("## 抱歉")
        or text == CLARIFY_STANDALONE_TEXT
        or text.startswith("未能找到")
        or "暂时无法直接处理" in text[:60]
        or "无法处理" in text[:60]
    )


def _funnel_note_resolved(clarify_click: dict | None, message: str,
                          session_id: str = "", trace_id: str = "") -> None:
    """点击追问卡的本轮以非拒答回答收尾 → resolved 双写（软失败）。

    身份显式传参：调用点在流收尾，ContextVar 双轨在 SSE 消费线程不可靠
    （实测 2026-10-03 落空 session 行）。
    """
    if not clarify_click or _looks_like_refusal_text(message):
        return
    try:
        from backend.observability.clarify_funnel import record_funnel_event

        record_funnel_event(
            "resolved", source=clarify_click.get("source", ""),
            session_id=session_id, trace_id=trace_id)
    except Exception:  # noqa: BLE001 — 漏斗旁路
        logger.debug("[Runner] resolved 记录失败", exc_info=True)


class GraphRunner:
    """统一图执行核心。"""

    def __init__(self, graph, memory, skill_nodes: set):
        self._graph = graph
        self._memory = memory
        self._skill_nodes = skill_nodes

    @_pin_prompt_snapshot
    def iter_events(
        self,
        question: str,
        session_id: str = "default",
        kb_id: str = "default",
        stop_event=None,
        user_id: str = "default",
        department: str = "",
        permissions: tuple[str, ...] | None = None,
        *,
        fallback_deltas: bool = True,
        model: str = "",
        domain_hint: str = "",
        tenant_id: str = "",
        idempotency_key: str = "",
        roles: tuple[str, ...] = (),
    ) -> Generator[dict, None, None]:
        """执行图并产出统一事件流。

        Args:
            fallback_deltas: 无真流式 delta 时是否用假打字机兜底呈现
                （SSE 模式 True；ask 模式 False，事件流仅用于取答案）
            model: 按请求模型覆盖（空 = 全局 LLM_MODEL；非法名在 bind 时忽略）
            domain_hint: 入口域提示（customer_service = 客服窗口锁域，
                router_node 据此强制走 CS 预过滤并跳过其他域图 prefilter）
        """
        from backend.observability.tracer import SpanKind, trace_collector

        start_time = time.time()

        # ── 追问漏斗：选项点击检测（2026-10-03 企业口径）──────────────
        # 前端点选项 = 原文重发；命中上一张卡的选项暂存即计 clicked。
        # 热路径代价 = 1 次缓存 GET（TwoTier 有 30s L1），软失败恒 None。
        clarify_click = None
        try:
            from backend.orchestration.graph.clarify_content import (
                consume_clarify_click,
            )

            clarify_click = consume_clarify_click(session_id, question or "")
            if clarify_click is not None:
                from backend.observability.clarify_funnel import (
                    record_funnel_event,
                )

                # trace 尚未建立（点击检测在 Guard 之前），身份必须显式传
                record_funnel_event(
                    "clicked", source=clarify_click.get("source", ""),
                    session_id=session_id)
                logger.info(
                    "[Runner] 追问卡选项点击: source=%s q=%.40s",
                    clarify_click.get("source", ""), question or "")
        except Exception:  # noqa: BLE001 — 漏斗旁路绝不阻断主链
            clarify_click = None

        # 请求开始先追平 Prompt Runtime epoch，再固定本次请求版本；Redis
        # 不可用时该检查 fail-open，继续使用当前快照。
        try:
            from backend.prompts.hot_reload import ensure_prompt_snapshot_fresh

            ensure_prompt_snapshot_fresh()
        except Exception:  # noqa: BLE001 - 热更新旁路不得阻断主链路
            logger.debug("[Runner] Prompt Runtime freshness check skipped", exc_info=True)

        # ── Input Guard：输入侧门禁（Router/Planner 之前；拦截即短路不进图）──
        guard_result = get_input_guard().guard(question or "", session_id=session_id)
        if guard_result.action in (GuardAction.BLOCK, GuardAction.CLARIFY):
            # 显式转人工豁免（2026-09-17）：guard 的「模糊问题→clarify」规则
            # 会把「转人工/找真人客服」类硬意图短路在图外（13ms 内直接回澄清
            # 话术），cs_prefilter 直通层永远收不到。此处仅豁免体验层的
            # CLARIFY——改写为 ALLOW 放行进图，由 cs_prefilter 直通路由到
            # cs_handoff；BLOCK（安全拦截：注入/有害）不豁免。
            if (
                guard_result.action == GuardAction.CLARIFY
                and (
                    _is_explicit_handoff(question or "")
                    or _is_confirmation_text(question or "")
                    or _should_bypass_guard_for_human_relay(
                        guard_result,
                        domain_hint=domain_hint,
                        user_id=user_id,
                        session_id=session_id,
                    )
                )
            ):
                logger.info(
                    "[Runner] 转人工/确认/人工接管期豁免 input_guard clarify，放行进图: "
                    f"{(question or '')[:40]}"
                )
                guard_result = guard_result.model_copy(update={
                    "action": GuardAction.ALLOW,
                    "confidence": 1.0,
                    "reason": f"customer_service_relay_bypass: {guard_result.reason}",
                })
            else:
                finish_guard_trace(session_id, guard_result)
                yield {"event": "status", "data": {"node": "input_guard", "ts": time.time()}}
                # ── L1 业务化追问接管（2026-09-19 拒答转追问）────────
                # guard 的「模糊/看不懂→clarify」只会给数据查询示例的通用
                # 提示；若弱命中业务域（如"帮我做个行程"差一个目的地槽位），
                # 优先给业务定向追问卡片。未命中业务倾向则保持 guard 原话术。
                clarify = None
                try:
                    from backend.orchestration.graph.clarify_content import (
                        CLARIFY_STANDALONE_TEXT,
                        build_entry_clarify,
                        clarify_allowed,
                        mark_clarified,
                    )

                    if clarify_allowed(session_id, question or ""):
                        clarify = build_entry_clarify(question or "", domain_hint)
                        if clarify is not None:
                            mark_clarified(
                                session_id, question or "",
                                options=clarify.get("options"),
                                source=clarify.get("source", ""),
                            )
                except Exception as e:
                    logger.warning(f"[Runner] 入口追问判定失败，保持 guard 原话术: {e}")
                    clarify = None
                if clarify is not None:
                    logger.info(
                        "[Runner] guard clarify 接管为业务追问: "
                        f"source={clarify.get('source')}"
                    )
                    try:
                        from backend.orchestration.context.routing_context import (
                            set_pending_question,
                        )

                        set_pending_question(
                            tenant_id, user_id, session_id,
                            clarify.get("question") or "",
                        )
                    except Exception:
                        logger.debug("[Runner] 待答问题记录失败（软降级）",
                                     exc_info=True)
                    yield {"event": "clarification", "data": {
                        "question": clarify["question"],
                        "options": [
                            {"id": f"option-{i}", "label": label}
                            for i, label in enumerate(clarify["options"], start=1)
                        ],
                        "handoff_available": clarify["handoff_available"],
                        "source": clarify.get("source", ""),
                        "ts": time.time(),
                    }}
                    try:
                        from backend.observability.clarify_funnel import (
                            record_funnel_event,
                        )

                        record_funnel_event(
                            "shown", source=str(clarify.get("source", "")),
                            session_id=session_id)
                    except Exception:  # noqa: BLE001 — 漏斗旁路
                        pass
                    message = CLARIFY_STANDALONE_TEXT
                else:
                    message = guard_result.message or "## 提示\n\n无法处理该问题。"
                if fallback_deltas:
                    yield from emit_delta_events(message, stop_event)
                yield {"event": _ANSWER_EVENT, "data": {"answer": message}}
                _funnel_note_resolved(clarify_click, message, session_id=session_id)
                # 拦截提示非模型回答，归因=系统提示（前端标「系统提示」徽章）
                yield make_done_event(message, {}, start_time,
                                      reply_source="system_notice",
                                      answer_source="input_guard")
                return

        # ── CS 窗口业务门禁：全局 Guard 放行后、域检测/子图之前 ──
        # 客服窗口已由 domain_hint 明确锁域。SCOPE/SENSITIVE 等客服业务
        # 规则不属于平台级 InputGuard，必须在这里执行，不能等待域检测结果；
        # 否则域检测超时/漏判会把越权输入放进主图。
        if (domain_hint or "").strip().lower() in {"customer_service", "cs"}:
            from backend.customer_service.security.input_guard import (
                GuardAction as CSGuardAction,
                get_cs_input_guard,
            )

            cs_guard_result = get_cs_input_guard().check(
                getattr(guard_result, "normalized_query", "") or question or "",
            )
            if cs_guard_result.action in (
                CSGuardAction.BLOCK,
                CSGuardAction.CLARIFY,
            ):
                finish_customer_service_guard_trace(session_id, cs_guard_result)
                yield {
                    "event": "status",
                    "data": {"node": "cs_input_guard", "ts": time.time()},
                }
                message = cs_guard_result.message or "无法处理该客服请求。"
                if fallback_deltas:
                    yield from emit_delta_events(message, stop_event)
                yield {"event": _ANSWER_EVENT, "data": {"answer": message}}
                _funnel_note_resolved(clarify_click, message, session_id=session_id)
                yield make_done_event(message, {}, start_time,
                                      reply_source="system_notice",
                                      answer_source="cs_input_guard")
                return

        # ── Tracing（提前到会话加载之前，使 memory/kb 加载耗时可归因）──
        trace = trace_collector.start(question, session_id, workflow_name="agent")
        # 请求级 Prompt 版本 pin（治理 #5）：本次执行所用版本在开始时定格，
        # 发布中途换版不影响已快照的值；trace 按此 tag 回答「当时用的哪版」。
        try:
            from backend.prompts.hot_reload import prompt_runtime_metadata
            from backend.prompts.service import prompt_service

            _pv = prompt_service.current_versions()
            trace.tags["prompt_versions"] = ",".join(
                f"{k}={v}" for k, v in sorted(_pv.items())[:12]) or "none"
            trace.tags["prompt_version"] = trace.tags["prompt_versions"]
            trace.metadata["prompt_versions"] = dict(_pv)
            runtime = prompt_runtime_metadata(_pv)
            trace.tags["prompt_runtime"] = runtime
            trace.tags["prompt_epoch"] = runtime["epoch"]
            trace.tags["snapshot_time"] = runtime["snapshot_time"]
            trace.tags["reload_source"] = runtime["reload_source"]
        except Exception:
            _pv = {}
        trace_collector.start_span("root", parent_id=None,
                                   name="多 Agent 协作管线", type="workflow",
                                   input={"question": question, "kb_id": kb_id})
        add_guard_span(guard_result)

        # 会话/记忆加载埋点
        load_span = trace_collector.start_span(
            "session_load", name="会话/记忆加载", type="workflow",
            kind=SpanKind.KB_ROUTING.value,
            input={"session_id": session_id})
        # STOP G observability：memory.retrieve span 贯穿（§七）。span 须在
        # 本侧创建——start_session 协程跑在 memory 后台 loop，无 ambient
        # trace；阶段计数由 service 经 l1.retrieval_stats 带回（仅
        # count/threshold 枚举，无记忆原文/PII）。
        retrieve_span = trace_collector.start_span(
            "memory.retrieve", name="长期记忆检索", type="retrieval",
            input={"session_id": session_id})
        try:
            l1 = self._memory.start_session(session_id, question, user_id=user_id,
                                            tenant_id=tenant_id)
            trace_collector.end_span(
                retrieve_span, metrics=dict(getattr(l1, "retrieval_stats", {}) or {}))
            initial_state = make_initial_state(
                question, session_id, kb_id, l1.messages,
                guard_result=guard_result.model_dump(mode="json"),
                user_id=user_id, department=department,
                domain_hint=domain_hint, tenant_id=tenant_id,
            )
            # 请求级 Prompt 版本随 state 流动（AgentState.prompt_versions，
            # checkpointer 开启时持久化——Celery 断点续跑恢复原版本）
            initial_state["prompt_versions"] = _pv
        except Exception as e:
            trace_collector.end_span(retrieve_span, status="error",
                                     metrics={"error": str(e)[:100]})
            trace_collector.end_span(load_span, status="error",
                                     metrics={"error": str(e)[:100]})
            yield {"event": "error", "data": {"message": f"会话加载失败: {e}", "ts": time.time()}}
            _end_root(trace, status="error", metrics={"error": "session_load_failed"})
            trace_collector.finish(trace, "",
                                   int((time.time() - start_time) * 1000), "", "")
            return
        trace_collector.end_span(
            load_span, metrics={"history_messages": len(l1.messages)})

        # ── FollowUpResolver（P2.7/D1）：Router 之前统一 Query Preprocessing ──
        # raw_query → ConversationContext → standalone_query → router_node。
        # 客服窗口锁域（domain_hint=customer_service）不参与解析：窗口内
        # 问法以订单/售后为主，指代改写与澄清会误伤客服语义。
        # 软失败：resolver 任何异常都回退原文，绝不阻断主链。
        if (domain_hint or "").strip().lower() not in ("customer_service", "cs"):
            effective_question = _resolve_followup_for_request(
                trace=trace,
                raw_question=question,
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                l1_messages=l1.messages,
            )
            if effective_question is None:
                # need_clarification：无上下文时禁止瞎猜（P2.9）。
                # 走既有 answer/done 通道输出澄清，不新增 SSE 事件类型。
                message = "你指的是哪个城市或地区？"
                logger.info(
                    "[Runner] follow-up 无上下文，澄清接管: session=%s", session_id)
                yield {"event": "status", "data": {
                    "node": "follow_up_resolver", "ts": time.time()}}
                if fallback_deltas:
                    yield from emit_delta_events(message, stop_event)
                yield {"event": _ANSWER_EVENT, "data": {"answer": message}}
                _funnel_note_resolved(clarify_click, message, session_id=session_id)
                # 澄清追问是系统发起的引导语，非模型回答
                yield make_done_event(message, {}, start_time,
                                      reply_source="system_notice",
                                      answer_source="follow_up_resolver")
                _end_root(trace, status="success",
                          metrics={"follow_up": "clarified"})
                trace_collector.finish(
                    trace, message,
                    int((time.time() - start_time) * 1000), "", "")
                return
            if effective_question != question:
                # P2.8：Router/各域消费 standalone；原始输入保留在
                # state["raw_query"] 供 trace / UI / debugging。
                initial_state["question"] = effective_question
                initial_state["raw_query"] = question

        # ── Context Assembler（路由入口重构 2026-09-22）──────────────
        # Guard 之后、Router 之前组装 Router 可读的会话上下文
        # （active_domain / last_intent / last_action / brief_summary /
        # pending_question），注入 state 供 ContinuationResolver 与粗分类
        # 消费。软失败：空上下文 = 无活跃任务，路由行为不劣化。
        try:
            from backend.orchestration.context.routing_context import (
                assemble_routing_context,
            )

            initial_state["routing_context"] = assemble_routing_context(
                tenant_id, user_id, session_id)
        except Exception as e:
            logger.warning(f"[Runner] routing_context 组装失败（软降级）: {e}")
            initial_state["routing_context"] = {}

        # ── worker 线程执行图 + 事件合并队列 ──
        # merged_q 元素: ("evt", event_dict) 或 ("done", None) 哨兵
        merged_q: queue.Queue = queue.Queue()
        # flush 早期 context 事件（L2 历史裁剪在 MemoryManager 后台线程发生，
        # 无 sink → 会话分桶缓冲；此处只补发【本会话】的事件。2026-10-01
        # STOP C 请求隔离：任意 runner 不再整队取走他人晚到事件）
        from backend.context_budget.metrics import drain_pending_events
        for _early in drain_pending_events(session_id=session_id):
            merged_q.put(("evt", {"event": "context", "data": _early}))
        # 请求上下文：trace/sink 显式持有并随状态流动，Send 分支经
        # trace_middleware 从 state 重新绑定（ContextVar 不跨线程继承）
        # deadline：在线请求统一预算（tool_runtime 治理），后台/测试路径无此对象不受约束
        from backend.core.tool_runtime.deadline import RequestDeadline
        # roles → data_scope：authorization 单一来源折算，随 checkpoint_safe
        # 序列化（SQL 等数据面消费）；未声明角色 → data_scope 为空（消费方 fail-closed）
        from backend.security.authorization import widest_data_scope
        request_ctx = RequestContext(
            session_id=session_id, user_id=user_id, kb_id=kb_id,
            tenant_id=tenant_id, idempotency_key=idempotency_key,
            department=department, permissions=permissions,
            roles=tuple(roles),
            data_scope=widest_data_scope(tuple(roles)) or "",
            trace=trace, model=model,
            deadline=RequestDeadline.started_now())
        ctx = {
            "final_answer": "",
            "answer_source": "",
            "all_step_results": {},
            "current_plan": dict(initial_state.get("plan", {})),
            "plan_changed": False,
            "route_mode": "plan",   # fix f8：捕获 Router 决策，trace 拓扑按模式构建
            "cs_context_snapshot": {},
            "usage": None,
            "aborted": False,       # 用户中止（worker 内检测）
            "worker_error": False,  # 图执行异常（worker 已 yield error 事件）
            "memory_persisted": False,
        }
        streamed = [False]  # 本轮是否发生过真流式 delta

        def _sink(text: str, kind: str = "answer") -> None:
            """proxy 流式 sink：生成 chunk 增量直接入队（worker/LangGraph 线程调用）。

            kind="thinking" → 推理模型思考链增量，走独立 thinking 事件；
            不置 streamed[0]（思考链不算回答输出，全思考零回答时仍走假打字机兜底）。
            """
            if stop_event is not None and stop_event.is_set():
                return
            if kind == "thinking":
                merged_q.put(("evt", {
                    "event": "thinking", "data": {"content": text, "ts": time.time()},
                }))
                return
            streamed[0] = True
            merged_q.put(("evt", {
                "event": "delta", "data": {"content": text, "ts": time.time()},
            }))

        request_ctx.stream_sink = _sink if ENABLE_TOKEN_STREAMING else None
        # 主图 checkpointer 感知：开启时 state 里只能放可序列化的 dict 形态
        # （trace/sink 进不了 checkpoint），节点入口经 get_context_from_state 还原
        has_checkpointer = getattr(self._graph, "checkpointer", None) is not None
        if has_checkpointer:
            initial_state["request_context"] = request_ctx.checkpoint_safe()
        else:
            put_context(initial_state, request_ctx)

        def _worker() -> None:
            # ContextVar 不跨线程：worker 入口整体绑定一次请求上下文；
            # Send 内部线程分支由节点入口的 bind_from_state 覆盖
            request_ctx.bind()
            # STOP CS-A P0-3：把 /chat/abort 的 stop_event 绑进请求上下文
            # （ContextVar 随 copy_context 传播进域图节点与专家线程），
            # CS 图内各取消检查边界据此在节点/调用边界提前终止
            from backend.core.request_context import bind_cancel_event

            bind_cancel_event(stop_event)
            # context SSE 事件 sink（L1/L2/L3 压缩发生时发声，2026-09-22）：
            # worker 线程内挂载（ContextVar 不跨线程继承），退出时还原
            from backend.context_budget.metrics import (
                reset_context_sink,
                set_context_sink,
            )

            def _context_sink(evt: dict) -> None:
                merged_q.put(("evt", {"event": "context", "data": evt}))

            _sink_token = set_context_sink(_context_sink)
            try:
                # recursion_limit：超限时 LangGraph 抛 GraphRecursionError 而非
                # 静默挂起（supervisor 自身 10 轮上限之外的最后一道防线）。
                # thread_id：每轮唯一（session+毫秒+uuid），checkpoint 定位用于崩溃
                # 恢复/审计，不做跨轮状态合并（多轮记忆由 MemoryService 负责）。
                # uuid 后缀防同毫秒重复提交撞 thread_id（建议项 2026-09-21）
                invoke_config: dict = {"recursion_limit": MAIN_GRAPH_RECURSION_LIMIT}
                if has_checkpointer:
                    invoke_config["configurable"] = {
                        "thread_id": (
                            f"agent-{session_id}-{int(time.time() * 1000)}"
                            f"-{uuid.uuid4().hex[:8]}"
                        ),
                    }
                for event in self._graph.stream(initial_state, config=invoke_config):
                    if stop_event is not None and stop_event.is_set():
                        ctx["aborted"] = True
                        break

                    node_name, node_output = _parse_event(event)
                    if node_name is None:
                        continue

                    merged_q.put(("evt", {"event": "status",
                                          "data": {"node": node_name, "ts": time.time()}}))
                    if node_name in self._skill_nodes or node_name in (
                            "skill_executor", "workflow_executor"):
                        merged_q.put(("evt", {"event": "log", "data": {
                            "level": "info",
                            "node": node_name,
                            "step_id": "tool_start",
                            "message": "正在调用数据工具" if node_name in self._skill_nodes
                            else "正在执行任务步骤",
                            "payload": {
                                "phase": "tool_start",
                                "tool": node_name,
                            },
                            "ts": time.time(),
                        }}))
                    for evt in stream_node_events(
                            node_name, node_output, self._skill_nodes,
                            make_step_payload, make_step_log_event):
                        merged_q.put(("evt", evt))

                    # 域图节点（cs_graph_node / travel_graph_node / 未来新增域）
                    # 统一从注册表取——加新域不必回来改这份白名单。
                    # （曾漏掉 travel_graph_node：域图跑完产出 903 字行程，
                    # 主图却因白名单不含它而丢弃 final_answer，前端只看到
                    # 「未能获取任何有效数据」兜底文案。）
                    if node_name in self._skill_nodes or node_name == "supervisor" \
                            or node_name in ("workflow_executor", "skill_executor",
                                             "cs_knowledge", "cs_pending",
                                             "general_chat") \
                            or node_name in _domain_node_names():
                        ctx["all_step_results"].update(node_output.get("step_results", {}))
                        # direct/workflow executor 自己就是最终产出者
                        executor_answer = node_output.get("final_answer", "")
                        if executor_answer:
                            # 类型兜底:final_answer 必须是 str(direct executor
                            # 的结构化输出已在源头渲染,此处仅防御)
                            if not isinstance(executor_answer, str):
                                executor_answer = str(executor_answer)
                            ctx["final_answer"] = executor_answer
                            ctx["answer_source"] = node_name
                    elif node_name == "reporter":
                        # Direct / Workflow 的 reporter 只做确定性业务结果渲染，
                        # 其输出覆盖 executor 的旧拼接答案；Plan 仍由 reporter 汇总。
                        reporter_answer = node_output.get("final_answer", "")
                        if reporter_answer and (
                                ctx["route_mode"] in {"direct", "workflow"}
                                or not ctx["final_answer"]):
                            ctx["final_answer"] = reporter_answer
                            ctx["answer_source"] = "reporter"
                    elif node_name == "router":
                        # fix f8：捕获路由模式供 trace 拓扑快照使用
                        if node_output.get("route_mode"):
                            ctx["route_mode"] = node_output["route_mode"]
                        # CS: 捕获 cs_context 供 trace 拓扑快照使用
                        if node_output.get("cs_context"):
                            ctx["cs_context_snapshot"] = node_output["cs_context"]
                    elif node_name == "cs_graph_node":
                        # Phase 4: CS Graph 适配器输出合并后的 cs_context
                        if node_output.get("cs_context"):
                            ctx["cs_context_snapshot"] = node_output["cs_context"]
                        # P3.1：等待确认的 pending_action → done 帧下发 CSConfirmCard
                        ctx["cs_pending_action"] = node_output.get("cs_pending_action") or None
                    elif node_name in ("planner", "critique"):
                        # 捕获 plan 用于 trace 重建
                        if node_output.get("plan"):
                            ctx["current_plan"] = node_output["plan"]
                        if node_output.get("_plan_changed"):
                            ctx["plan_changed"] = True

                    # P1: todo 快照 —— 任务列表变化节点跑完后，同步全量快照给前端。
                    # 位置在 all_step_results.update 之后：supervisor 轮次能带上最新步骤状态。
                    if node_name in ("planner", "critique", "supervisor"):
                        plan_nodes = (ctx.get("current_plan") or {}).get("nodes") or {}
                        if plan_nodes:
                            merged_q.put(("evt", make_todo_event(plan_nodes, ctx["all_step_results"])))
                    # P1: 流中用量 —— supervisor 每轮调度后透出累计用量（粒度 = supervisor 轮数）
                    if node_name == "supervisor":
                        usage_evt = make_usage_event()
                        if usage_evt:
                            merged_q.put(("evt", usage_evt))

                # 用量 ContextVar 在 worker 上下文累计，必须就地汇总
                ctx["usage"] = summarize_turn_usage()
            except ContextBudgetExceededError as e:
                # 超长输入硬门禁（2026-10-01 STOP A）：稳定错误码 + 用户可
                # 操作提示，无堆栈、retryable=False；provider 调用次数为 0。
                logger.warning(f"[GraphRunner] 输入超预算被硬门禁拒绝: {e}")
                ctx["worker_error"] = True
                merged_q.put(("evt", {"event": "error", "data": e.to_sse_data()}))
            except Exception as e:
                logger.error(f"[GraphRunner] 流式执行失败: {e}", exc_info=True)
                ctx["worker_error"] = True
                # Internal Runtime Status ≠ User-facing Error Message：
                # 用户帧只保留单行可读原因；堆栈尾 12 行此前直接拼进
                # message 被前端原样渲染（STOP H 收口），全栈只在日志
                # （上一行 exc_info=True）与 trace 留存。
                merged_q.put(("evt", {"event": "error",
                                      "data": {"message": f"执行失败: {e}",
                                               "ts": time.time()}}))
            finally:
                reset_context_sink(_sink_token)
                reset_stream_sink()
                merged_q.put(("done", None))

        threading.Thread(target=_worker, daemon=True, name="graph-worker").start()

        def _finalize_trace(status: str, metrics: dict | None = None,
                            answer: str | None = None) -> None:
            """trace 幂等收尾（2026-09-23 P0-3）：每轮恰好 finish 一次。

            统一承载四条退出路径：正常完成 / 用户中止（/chat/abort 或 stop）/
            客户端断连（生成器被 close，GeneratorExit）/ 内部异常。此前各分支
            各自 _end_root+finish，而「生成器被关闭」路径什么都不执行——该轮
            trace 永不落库、root span 泄漏在内存 collector 里，中止/断连恰是
            流式最常见的退出方式。

            STOP CS-A P0-3（F4）：用户主动中止的终态语义 = cancelled（不再
            伪装成 error），并落 cancel_source / cancel_stage 归因标签。
            """
            if ctx.get("trace_finalized"):
                return
            ctx["trace_finalized"] = True
            try:
                if status == "cancelled":
                    trace.tags["cancel_source"] = "user"
                    stage = (
                        (ctx.get("cs_context_snapshot") or {}).get("cancel_stage")
                        or ctx.get("cancel_stage")
                        or "graph_boundary"
                    )
                    trace.tags["cancel_stage"] = str(stage)
                    trace.metadata["cancelled"] = True
                _end_root(trace, status=status, metrics=metrics or {})
                trace_collector.finish(
                    trace,
                    answer if answer is not None else (ctx["final_answer"] or ""),
                    int((time.time() - start_time) * 1000), "", "")
                # 线上问题台账（2026-10-08 #13）：主图+客服窗口流量统一在此
                # 入账（域由 #12 三分类推导，plan 支线细分 planner）。主图
                # trace.question 是明文——入账前必须过 PII 掩码唯一出口；
                # 独立兜底，台账断流不能影响上面的 trace 收尾。
                try:
                    from backend.observability.question_ledger import (
                        record_question_from_trace,
                    )
                    from backend.shared.pii_mask import mask_pii

                    _masked_q, _vault = mask_pii(
                        str(getattr(trace, "question", "") or ""))
                    record_question_from_trace(
                        trace, question=_masked_q,
                        answer_summary=str(ctx.get("final_answer") or ""),
                    )
                except Exception:
                    logger.debug("[GraphRunner] 问题台账写入失败", exc_info=True)
            except Exception:
                logger.debug("[GraphRunner] trace 收尾失败", exc_info=True)

        try:
            while True:
                kind, evt = merged_q.get()
                if kind == "done":
                    break
                yield evt

            # ── 图执行结束后的收尾 ──
            # abort 可能发生在图已产出答案、但 Runner 尚未发 done 的窗口；
            # 此时取消优先，避免把未完成的本轮写入 Memory。
            if stop_event is not None and stop_event.is_set():
                ctx["aborted"] = True
            if ctx["aborted"]:
                yield {"event": "error", "data": {"message": "用户中止", "ts": time.time()}}
                # STOP CS-A P0-3/F4：trace 终态 = cancelled（cancel_source= user）
                _finalize_trace("cancelled", {"reason": "user_abort"})
                return
            if ctx["worker_error"]:
                _finalize_trace("error", {"error": "graph_failed"})
                return

            answer = ctx["final_answer"]
            # 两轨统一：正常完成但无最终回答时，用 step_results 兜底汇总
            if not answer:
                answer = _fallback_summary_from_results(ctx["all_step_results"])
            if answer:
                # canonical answer 的唯一快照同时供 Trace、Memory、done 使用。
                ctx["final_answer"] = answer
                if not ctx["answer_source"]:
                    ctx["answer_source"] = "step_results"

            # 未发生过真流式输出 → 兜底假打字机（guard/降级/非 LLM 路径仍有增量呈现）
            if fallback_deltas and not streamed[0] and answer:
                yield from emit_delta_events(answer, stop_event)
                if stop_event is not None and stop_event.is_set():
                    yield {"event": "error", "data": {"message": "用户中止", "ts": time.time()}}
                    _finalize_trace("cancelled", {"reason": "user_abort"})
                    return

            # ── Tracing: 重建 span 树 ──
            state_for_trace = {
                "plan": ctx["current_plan"],       # 从 Planner/Critique 捕获，非初始空 plan
                "step_results": ctx["all_step_results"],
                "_supervisor_loop_count": _count_rounds_from_results(ctx["all_step_results"]),
                "_degraded_steps": set(),
                "_plan_changed": ctx["plan_changed"],
                "final_answer": answer,
                "route_mode": ctx["route_mode"],   # fix f8
                "cs_context": ctx["cs_context_snapshot"],
            }
            trace.tags["answer_source"] = ctx["answer_source"] or "unknown"
            trace.tags["delta_source"] = (
                "llm_stream" if streamed[0] else "runner_fallback"
            )
            trace_from_state(trace, state_for_trace)
            _finalize_trace("success", {"span_count": len(trace.spans) - 1},
                            answer=answer)

            _persist_cs_turn_if_needed(
                ctx["cs_context_snapshot"], session_id, question, answer, trace.id,
                tenant_id=tenant_id,
            )

            # 只在成功路径持久化一次。断连续传由 chat.py 重放同一流，不会
            # 重跑 Runner；中止/超时/异常在到达此处前退出，因此不写错误答案。
            if (answer and not ctx["memory_persisted"]
                    and not ctx["cs_context_snapshot"].get("conversation_id")):
                ctx["memory_persisted"] = True
                try:
                    self._memory.end_turn(
                        session_id, question, answer,
                        user_id=user_id, tenant_id=tenant_id,
                    )
                except Exception:
                    logger.warning("[GraphRunner] Memory end_turn 写入失败", exc_info=True)

            # 内部事件：ask() 从这里取最终回答（SSE 层过滤）
            yield {"event": _ANSWER_EVENT, "data": {"answer": answer}}
            _funnel_note_resolved(clarify_click, answer,
                          session_id=session_id, trace_id=str(getattr(trace, "id", "") or ""))

            # 上下文用量快照（2026-09-22）：done 携带 context_usage 供前端
            # 显示「上下文 xx%」。近似口径 = 本轮输入历史 + 步骤产出；
            # 精确的逐次 LLM preflight 用量见 trace 与 context 事件。
            context_usage = None
            try:
                from backend.context_budget import context_budget as _cb
                _extra = [
                    str(sr.get("output"))
                    for sr in ctx["all_step_results"].values()
                    if isinstance(sr, dict) and sr.get("output") is not None
                ]
                context_usage = _cb.calculate_usage(
                    messages=initial_state.get("messages"),
                    extra_texts=_extra,
                ).to_dict()
            except Exception:
                logger.debug("context_usage 计算失败，done 不携带", exc_info=True)

            yield make_done_event(answer, ctx["all_step_results"], start_time,
                                  usage=ctx["usage"],
                                  pending_action=ctx.get("cs_pending_action"),
                                  trace_id=trace.id,
                                  context_usage=context_usage,
                                  include_pending_action="cs_pending_action" in ctx,
                                  answer_source=ctx.get("answer_source") or "unknown")

        except GeneratorExit:
            # 消费方关闭生成器（客户端断连 / 用户中止后前端停止拉流）：
            # 生成器挂起在 yield 处收到 GeneratorExit，此前这里不执行任何
            # 收尾。GeneratorExit 处理内禁止 yield；收尾后必须重新抛出。
            reason = ("user_abort"
                      if (stop_event is not None and stop_event.is_set())
                      or ctx["aborted"] else "client_disconnect")
            # STOP CS-A P0-3/F4：用户中止 → cancelled；纯断连保持 error
            #（producer 继续跑完，不是取消语义）
            _finalize_trace(
                "cancelled" if reason == "user_abort" else "error",
                {"reason": reason},
            )
            raise
        except Exception as e:
            import traceback as _tb
            logger.error(f"[GraphRunner] 流式执行失败: {e}", exc_info=True)
            _tb_tail = "\n".join(_tb.format_exc().splitlines()[-12:])
            yield {"event": "error",
                   "data": {"message": f"执行失败: {e}\n{_tb_tail}", "ts": time.time()}}
            _finalize_trace("error", {"error": str(e)[:100]})


# =====================================================
# Guard trace 辅助
# =====================================================

def add_guard_span(guard_result) -> None:
    """把 Input Guard 判定记录为 span（挂在 root 下，埋点失败不影响主流程）。"""
    from backend.observability.tracer import trace_collector
    try:
        span = trace_collector.start_span(
            "input_guard", name="Input Guard", type="workflow",
            input={"query_len": len(guard_result.normalized_query)},
        )
        trace_collector.end_span(
            span,
            output={
                "action": guard_result.action.value,
                "category": guard_result.category.value,
                "risk_level": guard_result.risk_level.value,
                "confidence": guard_result.confidence,
                "reason": guard_result.reason,
            },
            metrics={
                "layer": guard_result.layer,
                "llm_consulted": guard_result.llm_consulted,
                "needs_permission": guard_result.needs_permission,
                "domain": guard_result.domain or "",
                "policy_version": guard_result.policy_version,
            },
            status="success",
        )
    except Exception:
        logger.debug("[GraphRunner] Guard span 记录失败", exc_info=True)


def finish_guard_trace(session_id: str, guard_result) -> None:
    """为被 Guard 拦截的请求产出一条最小 trace（留 rejected 证据）。

    安全约束：trace 的 question 不落拦截请求原文（可能是恶意/敏感内容），
    用类别占位符替代；原文取证信息在 Guard 审计日志（sha256 摘要）中。
    """
    from backend.observability.tracer import trace_collector
    try:
        trace = trace_collector.start(
            f"[guard-rejected:{guard_result.category.value}]",
            session_id, workflow_name="agent",
        )
        span = trace_collector.start_span(
            "input_guard", parent_id=None,
            name="Input Guard 拦截", type="workflow",
        )
        trace_collector.end_span(
            span,
            output={
                "action": guard_result.action.value,
                "category": guard_result.category.value,
                "risk_level": guard_result.risk_level.value,
                "confidence": guard_result.confidence,
            },
            metrics={"policy_version": guard_result.policy_version},
            status="success",
        )
        trace_collector.finish(
            trace, guard_result.message or "", 0, "", "",
        )
    except Exception:
        logger.debug("[GraphRunner] Guard 拦截 trace 记录失败", exc_info=True)


def finish_customer_service_guard_trace(session_id: str, guard_result) -> None:
    """为客服业务门禁拦截产出最小 trace，不记录敏感原文。"""
    from backend.observability.tracer import trace_collector

    try:
        category = getattr(guard_result.category, "value", "unknown")
        action = getattr(guard_result.action, "value", "unknown")
        trace = trace_collector.start(
            f"[cs-guard-rejected:{category}]",
            session_id,
            workflow_name="agent",
        )
        span = trace_collector.start_span(
            "cs_input_guard",
            parent_id=None,
            name="CS Input Guard 拦截",
            type="workflow",
        )
        trace_collector.end_span(
            span,
            output={
                "action": action,
                "category": category,
                "reason": guard_result.reason,
            },
            status="success",
        )
        trace_collector.finish(
            trace,
            guard_result.message or "",
            0,
            "",
            "",
        )
    except Exception:
        logger.debug(
            "[GraphRunner] CS Input Guard 拦截 trace 记录失败",
            exc_info=True,
        )


# =====================================================
# 持久化 / trace 收尾辅助
# =====================================================

def _persist_cs_turn_if_needed(
    cs_context: dict,
    session_id: str,
    question: str,
    answer: str,
    trace_id: str,
    tenant_id: str = "",
) -> None:
    """Fire-and-forget CS turn persistence — no-op when not a CS request.

    STOP CS-A P0-2：租户显式传递给 record_cs_turn（keyword-only 必传），
    优先取身份链的 tenant_id，cs_context 快照兜底；两者皆空时由
    conversation_store fail-closed 跳过落库。
    """
    if not cs_context or not cs_context.get("conversation_id"):
        return
    try:
        from backend.customer_service.conversation_store import record_cs_turn
        record_cs_turn(
            conversation_id=cs_context["conversation_id"],
            user_id=cs_context.get("authenticated_user_id", "anonymous"),
            question=question,
            answer=answer,
            trace_id=trace_id,
            cs_route=cs_context.get("cs_route"),
            tenant_id=str(tenant_id or cs_context.get("tenant_id") or ""),
        )
    except Exception:
        logger.debug("[GraphRunner] CS turn persistence failed", exc_info=True)


def _end_root(trace, output: dict = None, metrics: dict = None,
              status: str = "success"):
    """查找并结束 root span。"""
    from backend.observability.tracer import trace_collector
    for sp in trace.spans:
        if sp.parent_id is None:
            trace_collector.end_span(sp, output=output, metrics=metrics, status=status)
            return


def _fallback_summary_from_results(step_results: dict) -> str:
    """无最终答案时复用 Reporter 的纯规则渲染，避免 dict/异常原文外泄。"""
    from backend.agents.reporter.reporter import render_step_results_deterministically

    if not step_results:
        return "## 无结果\n\n未能获取任何有效数据。"
    return render_step_results_deterministically(step_results)


def _count_rounds_from_results(step_results: dict) -> int:
    """从 step_results 估算 supervisor 循环次数（事件流路径用）。"""
    if not step_results:
        return 0
    # 简单启发：每有一个完成的步骤算 1 轮
    completed = sum(1 for sr in step_results.values()
                    if sr.get("status") in ("success", "failed", "skipped"))
    return max(1, completed)


def trace_from_state(trace, state: dict):
    """从 LangGraph final_state 重建执行过程 Span 树。

    invoke() 是黑盒，不能逐节点 trace；此方法从最终状态中提取执行痕迹：
      - Planner → span(type=agent, kind=graph_node)
      - Critique → span(type=agent, kind=graph_node)
      - Supervisor Round N → span(type=workflow, kind=graph_loop)
      - Skill 执行 → span(type=agent, kind=internal)
      - Reporter → span(type=agent, kind=graph_node)
      - LangGraph 拓扑 → trace.graph
    """
    from backend.observability.tracer import Span

    plan = state.get("plan", {})
    nodes = plan.get("nodes", {})
    step_results = dict(state.get("step_results", {}))
    loop_count = state.get("_supervisor_loop_count", 0)
    degraded_steps = state.get("_degraded_steps", set())
    plan_changed = state.get("_plan_changed", False)
    # fix f9：direct/workflow 模式未走 planner/critique/supervisor，
    # 不合成这些节点的占位 span（旧实现会产出 0ms planner 噪声 span）。
    route_mode = state.get("route_mode") or "plan"

    # 合成兜底策略（2026-08-21 浏览器实测整改）：
    # 中间件 span 命名为 {node_name} 或 {node_name}:{step_id}，只有节点真正
    # 执行过才会产生 span。因此「存在中间件 span」即执行证据：
    #   - 已有精确时长 span → 不再重复合成；
    #   - 无 span → 节点未执行（如 direct 模式跳过 planner/critique/supervisor）
    #     → 不合成 0ms 占位噪声 span。
    existing_ids = {s.span_id for s in trace.spans}

    def _has_node_span(node: str) -> bool:
        return any(sid == node or sid.startswith(f"{node}:") for sid in existing_ids)

    def _has_middleware_span(step_id: str) -> bool:
        # 中间件 skill span 命名：{node_name}:{step_id}
        return any(sid.endswith(f":{step_id}") for sid in existing_ids)

    # ── Planner（仅在 plan 模式执行过且中间件 span 缺失时补）──
    if route_mode == "plan" and nodes and not _has_node_span("planner"):
        planner_span = Span(
            span_id="planner", parent_id="root",
            name="Planner 拆解", type="agent",
            status="success",
            metrics={"subtasks": len(nodes), "synthesized": True},
        )
        trace.spans.append(planner_span)

    # ── Critique ──
    if route_mode == "plan" and state.get("_plan_critiqued") and not _has_node_span("critique"):
        crit_span = Span(
            span_id="critique", parent_id="root",
            name="计划审查", type="agent",
            status="success",
            metrics={"plan_changed": plan_changed, "synthesized": True},
        )
        trace.spans.append(crit_span)

    # ── Supervisor Rounds（direct/workflow 模式无调度轮次）──
    # 执行证据：plan 内的 step 才有 supervisor 派发（direct 模式的
    # direct_* step 由 skill_executor 直接执行，plan.nodes 为空 → 0 轮）。
    executed_rounds = sum(
        1 for sid, sr in step_results.items()
        if sid in nodes and sr.get("status", "pending") not in ("pending", "running")
    ) if route_mode == "plan" else 0
    for r in range(1, min(loop_count, executed_rounds) + 1):
        if _has_node_span("supervisor"):
            break
        round_span = Span(
            span_id=f"supervisor-round-{r}", parent_id="root",
            name=f"调度轮次 {r}", type="workflow",
            status="success",
            metrics={"round": r, "synthesized": True},
        )
        trace.spans.append(round_span)

    # ── Skills（从 step_results 重建）──
    for step_id, sr in step_results.items():
        cap = sr.get("capability", "")
        status = sr.get("status", "pending")
        desc = sr.get("description", step_id)
        if status in ("pending", "running"):
            continue  # 未执行的不生成 span
        if _has_middleware_span(step_id):
            continue  # 中间件已记录真实时长 span，不重复合成

        # error 兜底：失败步骤无错误详情时给出可读信息，
        # 避免前端看到 metrics.error='' 的空 error span。
        err = sr.get("error", "") or (
            f"步骤执行失败（状态={status}）" if status not in ("success", "skipped") else "")
        skill_span = Span(
            span_id=f"skill-{step_id}", parent_id="root",
            name=desc, type="agent",
            status=status if status in ("success", "error", "skipped") else "error",
            metrics={
                "capability": cap,
                "error": err,
                "retry_count": sr.get("retries", 0),
                "synthesized": True,
            },
        )
        trace.spans.append(skill_span)

    # ── Reporter（执行过但中间件 span 缺失才补）──
    final_answer = state.get("final_answer", "")
    if final_answer and not _has_node_span("reporter"):
        reporter_span = Span(
            span_id="reporter", parent_id="root",
            name="Reporter 汇总", type="agent",
            status="success" if final_answer else "error",
            metrics={"answer_len": len(final_answer), "synthesized": True},
            output={"answer_preview": final_answer[:200]} if final_answer else None,
        )
        trace.spans.append(reporter_span)

    # ── Graph 拓扑 ──
    trace.graph = _build_graph_snapshot(state, loop_count, degraded_steps)

    # ── SLA ──
    trace.sla_threshold_ms = _sla_for_plan(nodes)


def _build_graph_snapshot(state: dict, loop_count: int,
                          degraded_steps: set) -> dict:
    """从执行状态构建 LangGraph 拓扑快照（按实际路由模式区分）。

    fix f8：旧实现固定输出 planner→critique→supervisor 拓扑，
    direct/workflow 模式实际未走这些节点，前端展示的拓扑与真实执行
    路径不符。现按 route_mode 分支构建。
    """
    from backend.orchestration.supervisor.scheduler import MAX_SUPERVISOR_LOOPS

    route_mode = state.get("route_mode") or "plan"
    if route_mode in ("direct", "workflow"):
        executor_id = "skill_executor" if route_mode == "direct" else "workflow_executor"
        executor_label = "直接执行" if route_mode == "direct" else "工作流执行"
        graph_nodes = [
            {"id": "router", "label": "路由决策"},
            {"id": executor_id, "label": executor_label},
            {"id": "reporter", "label": "Reporter 汇总"},
        ]
        graph_edges = [
            {"source": "router", "target": executor_id, "label": route_mode},
            {"source": executor_id, "target": "reporter", "label": "完成"},
        ]
        return {
            "nodes": graph_nodes,
            "edges": graph_edges,
            "max_loops": MAX_SUPERVISOR_LOOPS,
            "loop_count": loop_count,
            "degradation_triggered": len(degraded_steps) > 0 if degraded_steps else False,
        }

    # ── 短路直出模式（追问/域引导）：Router → Reporter，未执行任何 Skill ──
    if route_mode in ("clarify", "handoff"):
        return {
            "nodes": [
                {"id": "router", "label": "路由决策"},
                {"id": "reporter", "label": "Reporter 汇总"},
            ],
            "edges": [
                {"source": "router", "target": "reporter", "label": route_mode},
            ],
            "max_loops": 0,
            "loop_count": 0,
            "degradation_triggered": False,
        }

    # ── 域图模式：Router → 域图节点 → END（通过 registry 动态查找）──
    from backend.orchestration.domain_registry import domain_graph_registry
    domain = domain_graph_registry.get(route_mode)
    if domain:
        graph_nodes = [
            {"id": "router", "label": "路由决策"},
            {"id": domain.node_name, "label": domain.label},
        ]
        graph_edges = [
            {"source": "router", "target": domain.node_name, "label": route_mode},
        ]
        return {
            "nodes": graph_nodes,
            "edges": graph_edges,
            "max_loops": 0,
            "loop_count": 0,
            "degradation_triggered": False,
        }

    # ── plan 模式：Router → Planner → Critique → Supervisor → Skills → Reporter ──
    plan = state.get("plan", {})
    plan_nodes = plan.get("nodes", {})

    graph_nodes = [
        {"id": "router", "label": "路由决策"},
        {"id": "planner", "label": "Planner 拆解"},
        {"id": "critique", "label": "计划审查"},
        {"id": "supervisor", "label": "Supervisor 调度"},
    ]
    # 动态节点（plan 中的步骤）
    for sid, node_info in plan_nodes.items():
        graph_nodes.append({
            "id": f"skill-{sid}",
            "label": node_info.get("description", sid),
        })
    graph_nodes.append({"id": "reporter", "label": "Reporter 汇总"})

    graph_edges = [
        {"source": "router", "target": "planner", "label": "plan"},
        {"source": "planner", "target": "critique"},
        {"source": "critique", "target": "supervisor"},
    ]
    for sid in plan_nodes:
        graph_edges.append({"source": "supervisor", "target": f"skill-{sid}", "label": "dispatch"})
        graph_edges.append({"source": f"skill-{sid}", "target": "supervisor", "label": "完成"})
    graph_edges.append({"source": "supervisor", "target": "reporter", "label": "all_done"})

    return {
        "nodes": graph_nodes,
        "edges": graph_edges,
        "max_loops": MAX_SUPERVISOR_LOOPS,
        "loop_count": loop_count,
        "degradation_triggered": len(degraded_steps) > 0 if degraded_steps else False,
    }


def _sla_for_plan(plan_nodes: dict) -> int:
    """根据计划复杂度估算 SLA 阈值（ms）。

    每步预留 LLM 调用 + DB 查询时间:
      - 1 步 (纯 RAG): 30s
      - 2 步 (SQL+分析): 60s
      - 3+ 步 (复杂编排): 90s
    """
    n = len(plan_nodes)
    if n <= 1:
        return 30000
    if n <= 2:
        return 60000
    return 90000
