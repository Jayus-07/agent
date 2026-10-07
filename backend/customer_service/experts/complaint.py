"""customer_service/experts/complaint.py — ComplaintExpert

投诉处理 Expert：投诉检测 → 创建工单 → 安抚响应 → 触发转接。
从 graph/nodes.py cs_complaint 提取核心逻辑。

设计参考: docs/customer-service/langgraph-multi-expert-design.md §6.4
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from backend.customer_service.experts.base import ExpertResult, ExpertStatus
from backend.shared.logger import logger

_SEVERITY_PRIORITY = {
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
}


def _severity_to_priority(severity: str) -> str:
    return _SEVERITY_PRIORITY.get(severity, "medium")


def execute_complaint(
    user_message: str,
    state: dict[str, Any],
) -> ExpertResult:
    """ComplaintExpert 核心逻辑。

    Args:
        user_message: 用户原始问题
        state: CSGraphState（含 user_id / session_id / conversation_id）

    Returns:
        ExpertResult — response_draft 为安抚响应，data 含工单 + handoff 信息
    """
    from backend.customer_service.audit import build_audit_entry
    from backend.customer_service.handoff import HandoffState, transition as handoff_transition
    from backend.customer_service.service.complaint_service import get_complaint_service
    from backend.observability.metrics import record_cs_handoff

    user_id = state.get("user_id", "anonymous")
    session_id = state.get("session_id", "default")
    conversation_id = state.get("conversation_id", "")
    cs_context = state.get("cs_context", {}) or {}

    # 防重入：本会话已建过投诉工单则幂等返回，不再重复建单/触发转接
    # （supervisor 的"连续同名专家"检测只能拦相邻重复，拦不住
    #   complaint → knowledge → complaint 的交替重入）
    # 检查两处：① cs_context 内的幂等标记（同 turn）② handoff store 中
    # 本会话由投诉升级产生的活跃转接（跨 turn / 上下文被裁剪后）
    existing_ticket = cs_context.get("complaint_ticket_id")
    if not existing_ticket:
        try:
            from backend.customer_service.handoff_store import get_handoff_store
            store = get_handoff_store()
            active = None
            # 批次C 修正（P0 验收发现）：优先按会话查活跃转接 ——
            # 原按 user 的 get_active 是 LIMIT 1 无排序查询，同用户有
            # 多条历史未关闭 handoff 时返回任意一条，会漏判同会话重复投诉
            if conversation_id:
                active = store.get_active_by_conversation(conversation_id)
                if not isinstance(active, Mapping):
                    active = None
            if active is None and session_id and session_id != conversation_id:
                active = store.get_active_by_conversation(session_id)
                if not isinstance(active, Mapping):
                    active = None
            if active is None:
                active = store.get_active_handoff(user_id)
                if not isinstance(active, Mapping):
                    active = None
            if active and active.get("trigger_type") == "complaint_escalation":
                existing_ticket = active.get("ticket_id")
        except Exception:
            logger.warning(
                "[ComplaintExpert] handoff store 查询失败，跳过跨 turn 幂等检查",
                exc_info=True,
            )
            # P2 完整性观测（2026-10-07）：fail-open 是声明式取舍（拒绝投诉
            # 伤害核心诉求，且随后的建单写也在同一库族上），但跳闸必须可数
            try:
                from backend.observability import metrics as _m
                _m.cs_complaint_integrity_total.labels(kind="idempotency_check_skipped").inc()
            except Exception:  # pragma: no cover — 指标软失败
                pass
    if existing_ticket:
        logger.info(
            "[ComplaintExpert] 幂等返回: ticket=%s（会话已有投诉工单）",
            existing_ticket,
        )
        return ExpertResult(
            expert="complaint",
            status=ExpertStatus.SUCCESS.value,
            response_draft=(
                f"您的投诉工单（{existing_ticket}）已在处理中，"
                "专人正在跟进，我们会尽快给您答复。"
            ),
            data={
                "ticket_id": existing_ticket,
                "severity": cs_context.get("complaint_severity", "medium"),
                "duplicate": True,
            },
        )

    logger.info(
        "[ComplaintExpert] user_id=%s question=%s...",
        user_id, user_message[:60],
    )

    service = get_complaint_service()
    detection = service.detect_with_llm_fallback(user_message)

    # severity 溯源（2026-10-08 LLM 收口，P0-23）：rule vs llm_fallback
    # 必须可归因——工单/升级决策仍由规则矩阵消费 severity（本层不变）。
    severity_source = (
        "llm_fallback"
        if "llm_fallback" in (detection.matched_patterns or [])
        else "rule"
    )
    try:
        from backend.observability.tracer import trace_collector
        _t = trace_collector.current()
        if _t is not None:
            _t.tags["cs_severity_source"] = severity_source
    except Exception:
        pass

    # ── 投诉分级（2026-10-08 拍板 A+B 直做）──────────────────────
    # 此前任何进入本专家的输入都无条件建单+转人工——用户随手「投诉一下
    # 试试」也会占用人工队列。现在三级分流：
    #   1) 非投诉（LLM 兜底也否认）→ 安抚+引导，不建单不转人工
    #   2) medium 灰区 → LLM 仲裁（B 案）判定真伪与急迫度；同会话第二次
    #      命中投诉（complaint_collect_count≥1）不再仲裁直接升级——
    #      反复投诉=真不满，且仲裁语义由「是否升级」收敛为「何时升级」
    #   3) high/critical → 立即升级（现状流程）
    # 仲裁 fail-safe 偏人工：LLM 挂/格式无效 = 升级（维持旧行为）。
    collect_count = int(cs_context.get("complaint_collect_count") or 0)
    if not detection.is_complaint and collect_count == 0:
        logger.info(
            "[ComplaintExpert] 分级: 非投诉（路由误入/试探），安抚不建单 user=%s",
            user_id,
        )
        return ExpertResult(
            expert="complaint",
            status=ExpertStatus.SUCCESS.value,
            response_draft=(
                "看起来您可能遇到了需要反馈的问题。如果是商品、物流或服务"
                "方面的不满，请直接描述具体情况（订单号+问题），我会立即"
                "为您记录处理。"
            ),
            data={
                "severity": "low",
                "escalated": False,
                "complaint_classified": "not_complaint",
            },
        )

    escalate = detection.severity in ("high", "critical") or collect_count >= 1
    arbitration_reason = ""
    if not escalate:
        verdict = service.llm_escalate_arbitration(user_message)
        if verdict is None:
            escalate = True  # fail-safe 偏人工
            arbitration_reason = "arbitration_failed"
        else:
            escalate = bool(verdict.get("escalate"))
            arbitration_reason = str(verdict.get("reason", ""))

    if not escalate:
        # A 案收集：安抚 + 三要素追问，轻工单记录但不动人工队列
        ticket = service.create_ticket(
            user_id=user_id,
            conversation_id=conversation_id,
            severity=detection.severity,
            summary=user_message,
        )
        service.simulate_execute(ticket)
        # 轻工单同样落库镜像（可查询性；失败不阻断收集回复）
        try:
            from backend.customer_service.ticket_store import get_ticket_store

            get_ticket_store().create_sync(
                ticket_id=ticket.ticket_id,
                conversation_id=conversation_id or session_id,
                user_id=user_id,
                type="complaint",
                status="open",
                source="ai",
                severity=detection.severity,
                priority=_severity_to_priority(detection.severity),
                title=f"投诉收集（{detection.severity}）：{user_message[:80]}",
                description=user_message[:2000],
            )
        except Exception:
            logger.warning(
                "[ComplaintExpert] 收集工单落库失败（不阻断）: %s",
                ticket.ticket_id, exc_info=True,
            )
        logger.info(
            "[ComplaintExpert] 分级: medium 收集模式 ticket=%s reason=%s user=%s",
            ticket.ticket_id, arbitration_reason, user_id,
        )
        return ExpertResult(
            expert="complaint",
            status=ExpertStatus.SUCCESS.value,
            response_draft=service.build_collect_response(),
            data={
                "ticket_id": ticket.ticket_id,
                "severity": detection.severity,
                "escalated": False,
                "complaint_classified": "collect",
                "arbitration_reason": arbitration_reason,
            },
        )

    logger.info(
        "[ComplaintExpert] 分级: 升级人工 severity=%s reason=%s user=%s",
        detection.severity, arbitration_reason or "high_or_repeat", user_id,
    )
    ticket = service.create_ticket(
        user_id=user_id,
        conversation_id=conversation_id,
        severity=detection.severity,
        summary=user_message,
    )
    service.simulate_execute(ticket)

    # 批次C：投诉工单落库（此前仅内存模拟）。fire-and-forget：落库失败
    # 只损失可查询性，不阻断安抚回复与转人工。
    try:
        from backend.customer_service.ticket_store import get_ticket_store

        get_ticket_store().create_sync(
            ticket_id=ticket.ticket_id,
            conversation_id=conversation_id or session_id,
            user_id=user_id,
            type="complaint",
            status="open",
            source="ai",
            severity=detection.severity,
            priority=_severity_to_priority(detection.severity),
            title=f"投诉工单（{detection.severity}）：{user_message[:80]}",
            description=user_message[:2000],
        )
    except Exception:
        logger.warning(
            "[ComplaintExpert] 投诉工单落库失败（不阻断主流程）: %s",
            ticket.ticket_id,
            exc_info=True,
        )
        # P2 完整性观测：镜像行缺失意味着工单查询查不到该编号
        try:
            from backend.observability import metrics as _m
            _m.cs_complaint_integrity_total.labels(kind="ticket_mirror_write_failed").inc()
        except Exception:  # pragma: no cover — 指标软失败
            pass

    # 迁移 B12：统一案件 cs_case 并行写入（设计方案 §10.1「case 是唯一
    # 案件事实源」；与 tickets 并存过渡，全面并表走后续批次）。SLA 按
    # severity 映射优先级（critical→P0/high→P1/其余→P2）由服务层计算。
    # fire-and-forget：案件落库失败只损失可查询性，不阻断安抚与转人工。
    try:
        from backend.customer_service.case.service import get_case_service

        _SEVERITY_TO_PRIORITY = {"critical": "P0", "high": "P1"}
        get_case_service().create_sync(
            conversation_id=conversation_id or session_id,
            user_id=user_id,
            case_type="complaint",
            priority=_SEVERITY_TO_PRIORITY.get(detection.severity, "P2"),
            title=f"投诉案件（{detection.severity}）：{user_message[:80]}",
            context={
                "source": "complaint_expert",
                "ticket_id": ticket.ticket_id,
                "severity": detection.severity,
                "summary": user_message[:500],
            },
        )
    except Exception:
        logger.warning(
            "[ComplaintExpert] 投诉案件落库失败（不阻断主流程）",
            exc_info=True,
        )

    answer = service.build_comfort_response(detection, ticket)

    # P1 重构（2026-09-17）：投诉升级与显式转人工同流程 ——
    # 状态机内存转换 AI_ACTIVE→REQUESTED→WAITING_HUMAN，单次落盘
    # 最终态 waiting_human（此前卡 handoff_requested，坐席认领 409），
    # 并与 HandoffExpert 一致发布 conversation.waiting 实时事件。
    handoff_transition(HandoffState.AI_ACTIVE, HandoffState.HANDOFF_REQUESTED)
    handoff_transition(
        HandoffState.HANDOFF_REQUESTED, HandoffState.WAITING_HUMAN,
    )

    # STOP CS-A P0-6：与 HandoffExpert 同走 lifecycle 唯一入口 ——
    # 单事务建 waiting_human 工单 + conversations.handling_mode 投影同写
    # （旧 store.save 只写 handoffs 行，是双表口径分叉的两处之一）。
    from backend.customer_service.handoff.lifecycle import (
        enter_waiting_handoff_sync,
    )

    _tenant_id = str(state.get("tenant_id", "") or "")
    handoff_row = enter_waiting_handoff_sync(
        tenant_id=_tenant_id or "default",
        conversation_id=conversation_id or session_id,
        user_id=user_id,
        trigger_type="complaint_escalation",
        trigger_reason=f"投诉升级: severity={detection.severity}",
        ticket_id=ticket.ticket_id,
    )
    record_cs_handoff("complaint")

    # 实时推送：投诉工单进入坐席待接入队列（与显式转人工一致）
    from backend.customer_service.realtime import get_agent_hub

    get_agent_hub().publish(
        "conversation.waiting",
        tenant_id=_tenant_id or None,
        item={
            "conversation_id": conversation_id or session_id,
            "user_id": user_id,
            "tenant_id": _tenant_id or None,
            "handoff_state": HandoffState.WAITING_HUMAN.value,
            "trigger_type": "complaint_escalation",
            "trigger_reason": f"投诉升级: severity={detection.severity}",
            "updated_at": handoff_row["updated_at"],
            "last_message_preview": (user_message[:80] if user_message else None),
        },
    )

    audit_entry = build_audit_entry(
        user_id=user_id,
        action_type="complaint_ticket_created",
        result="success",
        target_type="complaint",
        target_id=ticket.ticket_id,
        detail=f"severity={detection.severity}, handoff triggered",
        conversation_id=conversation_id,
    )

    logger.info(
        "[ComplaintExpert] ticket=%s severity=%s handoff=WAITING_HUMAN",
        ticket.ticket_id, detection.severity,
    )

    return ExpertResult(
        expert="complaint",
        status=ExpertStatus.SUCCESS.value,
        response_draft=answer,
        data={
            "ticket_id": ticket.ticket_id,
            "severity": detection.severity,
            "escalated": True,
            "arbitration_reason": arbitration_reason,
            "handoff_state": HandoffState.WAITING_HUMAN.value,
            "handling_mode": "human",
            "audit_entry": audit_entry,
        },
    )


def complaint_expert_node(state: dict[str, Any]) -> dict[str, Any]:
    """CS Graph ComplaintExpert 节点函数。"""
    from backend.config.customer_service import CS_EXPERT_TIMEOUT_S
    from backend.customer_service.experts.base import run_expert_safely

    user_message = state.get("user_message", "")

    # 专家级兜底限时：内部各环节自有限时，此层保证整节点上界（与
    # knowledge/action 同口径）；不传则任一环节挂起即无上界
    result = run_expert_safely(
        expert_name="complaint",
        fn=lambda _state: execute_complaint(user_message, state),
        state=state,
        timeout_s=CS_EXPERT_TIMEOUT_S,
    )

    expert_history = list(state.get("expert_history", []))
    expert_history.append({
        "expert": "complaint",
        "status": result.get("status", "failed"),
        "duration_ms": result.get("duration_ms", 0),
    })

    audit_entries = list(state.get("cs_audit_entries", []))
    if result.get("status") == "success" and result.get("data", {}).get("audit_entry"):
        from backend.customer_service.audit import append_audit
        audit_entries = append_audit(audit_entries, result["data"]["audit_entry"])

    cs_context = dict(state.get("cs_context", {}))
    if result.get("data", {}).get("handoff_state"):
        cs_context["handoff_state"] = result["data"]["handoff_state"]
    if result.get("data", {}).get("handling_mode"):
        cs_context["handling_mode"] = result["data"]["handling_mode"]
    # 记录已建工单：供 complaint 专家幂等防重入（见 execute_complaint）。
    # 仅升级路径可作幂等键——收集态轻工单不锁后续投诉（第二次命中要能
    # 走到「直接升级」分支）。
    _d = result.get("data", {})
    if _d.get("ticket_id") and _d.get("escalated") and not _d.get("duplicate"):
        cs_context["complaint_ticket_id"] = _d["ticket_id"]
        cs_context["complaint_severity"] = _d.get("severity", "medium")
    # 投诉分级（2026-10-08）：收集模式计数（第二次命中直接升级），升级后清零
    data = result.get("data", {})
    if data.get("complaint_classified") == "collect":
        cs_context["complaint_collect_count"] = (
            int(cs_context.get("complaint_collect_count") or 0) + 1
        )
    elif data.get("escalated"):
        cs_context["complaint_collect_count"] = 0

    return {
        "last_expert_result": dict(result),
        "expert_history": expert_history,
        "cs_audit_entries": audit_entries,
        "cs_context": cs_context,
    }
