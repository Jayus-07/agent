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
            sections.append(_dispatch_service(user_id, it, user_message, cs_route))
        answer = "\n\n---\n\n".join(sections)
        return ExpertResult(
            expert="query",
            status=ExpertStatus.SUCCESS.value,
            response_draft=answer,
            data={"intent": intents, "user_id": user_id, "decomposed": True},
        )

    answer = _dispatch_service(user_id, intent, user_message, cs_route)

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
    from backend.customer_service.experts.base import run_expert_safely

    user_message = state.get("user_message", "")
    cs_route = state.get("cs_route", {})

    result = run_expert_safely(
        expert_name="query",
        fn=lambda _state: execute_query(user_message, cs_route, state),
        state=state,
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
) -> str:
    """根据 intent 分发到对应的 Service。"""
    service_type = _INTENT_SERVICE_MAP.get(intent, "order")

    if service_type == "order":
        from backend.customer_service.service.order_service import get_order_service
        order_service = get_order_service()

        if intent == "t_order_status":
            # P3.5：问句带具体订单号 → detail 精确查（此前一律全量列表，
            # 「DEMO-1001 到哪了」返回整个订单列表，答非所问）
            order_no = _extract_order_no(question)
            if order_no:
                try:
                    result = order_service.query_orders(
                        user_id=user_id, order_id=order_no, query_type="detail",
                    )
                    return _format_order_list(result.orders)
                except Exception:
                    return (
                        f"没有找到订单 {order_no} 的记录。请核对订单号，"
                        "或告诉我「查我的所有订单」，我来帮您列出全部订单。"
                    )
            result = order_service.query_orders(user_id=user_id)
            return _format_order_list(result.orders)
        return _format_order_list([])

    if service_type == "logistics":
        from backend.customer_service.service.logistics_service import get_logistics_service
        logistics = get_logistics_service()
        order_id = _get_latest_order_id(user_id)
        if order_id:
            result = logistics.query_logistics(user_id=user_id, order_id=order_id)
            return _format_logistics(result)
        return "暂无物流信息。请先查询您的订单。"

    return "该功能正在建设中，请稍后再试。"


def _extract_order_no(question: str) -> str | None:
    """P3.5：从问句提取订单号（DEMO-1002 / ORD-001 / ORD-20260915-0042 /
    纯数字长号）。

    与 action expert 各自独立提取（服务不同，耦合收益低）；识别不到
    返回 None 走全量列表语义。
    P0 实测修复（2026-09-19）：正则允许多段连字，两段式订单号此前被
    截断为首段（ORD-20260915-0042 → ORD-20260915）。
    """
    import re

    if not question:
        return None
    m = re.search(r"\b([A-Za-z]{2,10}(?:-\d{2,12})+)\b", question)
    if m:
        return m.group(1).upper()
    m = re.search(r"订单[号]?\s*[:：为]?\s*(\d{5,20})", question)
    if m:
        return m.group(1)
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
