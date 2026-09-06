"""customer_service/experts/query.py — QueryExpert

业务查询 Expert：订单查询、物流查询等只读操作。
从 graph/nodes.py cs_business_query 提取核心逻辑。

设计参考: docs/customer-service/langgraph-multi-expert-design.md §6.2
"""
from __future__ import annotations

from typing import Any

from backend.customer_service.experts.base import ExpertResult, ExpertStatus
from backend.shared.logger import logger

_INTENT_SERVICE_MAP = {
    "t_order_status": "order",
    "t_logistics": "logistics",
    "as_repair": "order",
    "as_quality_issue": "order",
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

    answer = _dispatch_service(user_id, intent, user_message, cs_route)

    return ExpertResult(
        expert="query",
        status=ExpertStatus.SUCCESS.value,
        response_draft=answer,
        data={"intent": intent, "user_id": user_id},
    )


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
