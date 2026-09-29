"""customer_service/experts/query.py — QueryExpert

业务查询 Expert：订单查询、物流查询等只读操作。
从 graph/nodes.py cs_business_query 提取核心逻辑。

设计参考: docs/customer-service/langgraph-multi-expert-design.md §6.2
"""
from __future__ import annotations

import json
from typing import Any

from backend.customer_service.experts.base import ExpertResult, ExpertStatus
from backend.shared.logger import logger

_INTENT_SERVICE_MAP = {
    "t_order_status": "order",
    "t_logistics": "logistics",
    "t_ticket_status": "ticket",  # 批次C：工单进度
    "as_repair": "order",
    "as_quality_issue": "order",
}

# 复合问题预判：问题命中 ≥2 个不同服务域的关键词 → 疑似复合诉求
_SERVICE_KEYWORDS = {
    "order": ["订单", "退款", "退货", "换货", "售后", "维修", "保修", "质量", "破损", "发票"],
    "logistics": ["物流", "快递", "发货", "收货", "签收", "配送", "到货", "运输", "到哪"],
}


def execute_query(
    user_message: str,
    cs_route: dict,
    state: dict[str, Any],
) -> ExpertResult:
    """QueryExpert 核心逻辑。

    Args:
        user_message: 用户原始问题
        cs_route: CS Router 输出（含 intent）
        state: CSGraphState（用于身份验证）

    Returns:
        ExpertResult — response_draft 为格式化的查询结果
    """
    from backend.customer_service.security.permission import PermissionChecker

    intent = cs_route.get("intent", "t_order_status")
    user_id = PermissionChecker.validate_user_identity(state)

    logger.info(
        "[QueryExpert] intent=%s user_id=%s question=%s...",
        intent, user_id, user_message[:60],
    )

    intents = None
    if _compound_suspected(user_message):
        intents = _llm_decompose_intents(user_message)

    if intents and len(intents) > 1:
        # 复合问题：逐意图查询后合并（同一服务去重，避免重复输出）
        sections: list[str] = []
        dispatched_services: list[str] = []
        for it in intents:
            service_type = _INTENT_SERVICE_MAP.get(it, "order")
            if service_type in dispatched_services:
                continue
            dispatched_services.append(service_type)
            sections.append(_dispatch_service(
                user_id, it, user_message, cs_route,
                tenant_id=str(state.get("tenant_id") or "default"),
                session_id=str(state.get("session_id") or ""),
            ))
        answer = "\n\n---\n\n".join(sections)
        return ExpertResult(
            expert="query",
            status=ExpertStatus.SUCCESS.value,
            response_draft=answer,
            data={"intent": intents, "user_id": user_id, "decomposed": True},
        )

    tenant_id = str(state.get("tenant_id") or "default")
    session_id = str(state.get("session_id") or "")
    answer = _dispatch_service(
        user_id, intent, user_message, cs_route,
        tenant_id=tenant_id, session_id=session_id,
    )

    return ExpertResult(
        expert="query",
        status=ExpertStatus.SUCCESS.value,
        response_draft=answer,
        data={"intent": intent, "user_id": user_id},
    )


def _compound_suspected(question: str) -> bool:
    """规则预判：关键词命中 ≥2 个服务域才触发 LLM 分解，控制成本。"""
    hits = {
        svc for svc, keywords in _SERVICE_KEYWORDS.items()
        if any(k in question for k in keywords)
    }
    return len(hits) >= 2


def _llm_decompose_intents(question: str) -> list[str] | None:
    """LLM 意图分解 → intent 列表。失败或格式无效返回 None（确定性降级）。

    仅在规则预判疑似复合问题时调用；单意图问题不付这笔 LLM 延迟。
    """
    try:
        from langchain_core.messages import HumanMessage

        from backend.config.customer_service import (
            CS_QUERY_LLM_DECOMPOSE_ENABLED,
            CS_QUERY_LLM_TIMEOUT_MS,
        )
        from backend.infra.async_utils import sync_call_with_timeout
        from backend.infra.llm import get_llm

        if not CS_QUERY_LLM_DECOMPOSE_ENABLED:
            return None

        prompt = (
            "你是客服意图分析器。用户的问题可能包含多个业务诉求，"
            "请从以下意图中选出问题实际涉及的项（可多选）：\n"
            "t_order_status（订单状态/退款/退货/售后）\n"
            "t_logistics（物流/快递/发货进度）\n"
            "as_repair（维修/保修申请）\n"
            "as_quality_issue（质量问题反馈）\n\n"
            f"用户问题: {question[:200]}\n"
            '只回复 JSON 数组，如 ["t_order_status", "t_logistics"]，不要解释。'
        )
        # config={"timeout"} 实测不生效（2026-09），须线程级限时
        response = sync_call_with_timeout(
            get_llm().invoke,
            CS_QUERY_LLM_TIMEOUT_MS / 1000.0,
            [HumanMessage(content=prompt)],
        )
        content = response.content.strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:]
        intents = json.loads(content)
        if not isinstance(intents, list):
            logger.warning("[QueryExpert] LLM 分解返回非数组，回退单意图")
            return None
        valid = [i for i in intents if isinstance(i, str) and i in _INTENT_SERVICE_MAP]
        if not valid:
            return None
        logger.info("[QueryExpert] 复合问题分解: %s", valid)
        return valid
    except Exception as e:
        logger.warning("[QueryExpert] LLM 意图分解失败，回退单意图: %s", e)
        return None


def query_expert_node(state: dict[str, Any]) -> dict[str, Any]:
    """CS Graph QueryExpert 节点函数。"""
    from backend.config.customer_service import CS_EXPERT_TIMEOUT_S
    from backend.customer_service.experts.base import run_expert_safely

    user_message = state.get("user_message", "")
    cs_route = state.get("cs_route", {})

    # 专家级兜底限时：内部各环节自有限时，此层保证整节点上界（与
    # knowledge/action 同口径）；不传则任一环节挂起即无上界
    result = run_expert_safely(
        expert_name="query",
        fn=lambda _state: execute_query(user_message, cs_route, state),
        state=state,
        timeout_s=CS_EXPERT_TIMEOUT_S,
    )

    expert_history = list(state.get("expert_history", []))
    expert_history.append({
        "expert": "query",
        "status": result.get("status", "failed"),
        "duration_ms": result.get("duration_ms", 0),
    })

    return {
        "last_expert_result": dict(result),
        "expert_history": expert_history,
    }


def _dispatch_service(
    user_id: str, intent: str, question: str, cs_route: dict,
    tenant_id: str = "default", session_id: str = "",
) -> str:
    """根据 intent 分发到对应的 Service。"""
    service_type = _INTENT_SERVICE_MAP.get(intent, "order")

    if service_type == "order":
        from backend.customer_service.service.order_service import get_order_service
        order_service = get_order_service()

        if intent == "t_order_status":
            # 缺陷9（2026-09-23）：回指承接解析出的订单号经 cs_route.metadata
            # 注入，优先于当前轮文本抽取——「那它到哪了」resolved_query 里
            # 的订单号是权威 referent。
            injected_order = str(
                (cs_route.get("metadata") or {}).get("order_id") or ""
            ).strip()
            # P3.5：问句带具体订单号 → detail 精确查（此前一律全量列表，
            # 「DEMO-1001 到哪了」返回整个订单列表，答非所问）
            order_no = injected_order or _extract_order_no(question)
            if order_no:
                try:
                    result = order_service.query_orders(
                        user_id=user_id, order_id=order_no, query_type="detail",
                    )
                except Exception:
                    return (
                        f"没有找到订单 {order_no} 的记录。请核对订单号，"
                        "或告诉我「查我的所有订单」，我来帮您列出全部订单。"
                    )
                # 缺陷9：精确查单返回唯一订单 → 记录会话业务上下文，
                # 供下一轮回指（「那它到哪了」）继承。列表查询不写
                # （多订单无唯一 referent，不得猜测）。
                if result.orders:
                    from backend.customer_service.context_resolver import (
                        record_recent_order,
                    )

                    record_recent_order(
                        tenant_id, user_id, session_id, order_no,
                        source_intent=intent,
                    )
                return _format_order_list(result.orders)
            result = order_service.query_orders(user_id=user_id)
            return _format_order_list(result.orders)
        return _format_order_list([])

    if service_type == "logistics":
        from backend.customer_service.service.logistics_service import get_logistics_service
        logistics = get_logistics_service()
        injected_order = str(
            (cs_route.get("metadata") or {}).get("order_id") or ""
        ).strip()
        # 缺陷9：会话上下文注入的订单号优先；_get_latest_order_id（按用户
        # 全局最近一单）是既有 fallback，保留原行为不在本轮扩大。
        order_id = injected_order or _get_latest_order_id(user_id)
        if order_id:
            result = logistics.query_logistics(user_id=user_id, order_id=order_id)
            return _format_logistics(result)
        return "暂无物流信息。请先查询您的订单。"

    if service_type == "ticket":
        # 批次C：工单进度查询（统一工单表，投诉/转人工落库后可查）
        try:
            from backend.customer_service.ticket_store import (
                get_ticket_store,
            )

            tickets = get_ticket_store().list_for_user_sync(
                user_id=user_id, limit=10,
            )
            return _format_ticket_list(tickets)
        except Exception:
            logger.warning("[QueryExpert] 工单查询失败", exc_info=True)
            return "暂时查不到您的工单信息，请稍后再试。"

    return "该功能正在建设中，请稍后再试。"


def _extract_order_no(question: str) -> str | None:
    """订单号提取（P1 收敛：委托 understanding.entities 单一事实源）。

    规范化文本上抽取（字母数字混合段/关键词纯数字），识别不到返回 None
    走全量列表语义。
    """
    from backend.customer_service.understanding.entities import extract_entities
    from backend.customer_service.understanding.types import EntityType
    from backend.security.input_guard.normalize import normalize_query

    for e in extract_entities(normalize_query(question or "")):
        if e.type == EntityType.ORDER_ID:
            return e.match()
    return None


def _get_latest_order_id(user_id: str) -> str | None:
    """获取用户最新订单 ID（用于无指定 order_id 的物流查询）。"""
    try:
        from backend.customer_service.service.order_service import get_order_service
        result = get_order_service().query_orders(user_id=user_id)
        if result.orders:
            return str(result.orders[0].get("id") or result.orders[0].get("order_no"))
    except Exception:
        pass
    return None


_TICKET_STATUS_LABELS = {
    "open": "已受理",
    "processing": "处理中",
    "pending_user": "等待您补充信息",
    "resolved": "已解决",
    "closed": "已关闭",
}

_TICKET_TYPE_LABELS = {
    "complaint": "投诉",
    "handoff": "人工服务",
    "inquiry": "咨询",
    "repair": "报修",
}


def _format_ticket_list(tickets: list[dict]) -> str:
    """格式化工单列表为 Markdown（批次C）。"""
    if not tickets:
        return (
            "您当前没有进行中的工单。如需帮助，可以描述您的问题，"
            "或回复「转人工」由客服人员为您处理。"
        )

    lines = ["## 您的工单\n"]
    for i, t in enumerate(tickets[:10], 1):
        status = _TICKET_STATUS_LABELS.get(t.get("status", ""), t.get("status", ""))
        type_label = _TICKET_TYPE_LABELS.get(t.get("type", ""), t.get("type", ""))
        title = (t.get("title") or type_label)[:40]
        lines.append(
            f"**{i}.** [{type_label}] {title} | "
            f"工单号: `{t.get('ticket_id', '')}` | "
            f"状态: {status}"
        )
    lines.append("\n如需了解工单详情，请回复工单号，或回复「转人工」咨询客服。")
    return "\n".join(lines)


def _format_order_list(orders: list[dict]) -> str:
    """格式化订单列表为 Markdown。"""
    if not orders:
        return "您当前没有相关订单记录。"

    lines = ["## 您的订单\n"]
    for i, o in enumerate(orders[:10], 1):
        order_no = o.get("order_no", "N/A")
        status = o.get("status", "未知")
        amount = o.get("total_amount", "0")
        created = o.get("created_at", "")
        if hasattr(created, "strftime"):
            created = created.strftime("%Y-%m-%d")
        else:
            created = str(created)[:10] if created else ""
        lines.append(
            f"**{i}.** 订单号: `{order_no}` | "
            f"状态: {status} | "
            f"金额: ¥{amount} | "
            f"时间: {created}"
        )

    if len(orders) > 10:
        lines.append(f"\n*仅显示最近 10 条，共 {len(orders)} 条订单。*")

    return "\n".join(lines)


def _format_logistics(result: Any) -> str:
    """格式化物流信息为 Markdown。"""
    lines = [
        "## 物流信息\n",
        f"**订单号:** `{result.order_no}`",
        f"**物流状态:** {result.status_display}",
    ]
    if result.tracking_info:
        lines.append(f"\n{result.tracking_info}")
    return "\n".join(lines)
