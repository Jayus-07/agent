"""customer_service/service/logistics_service.py — 物流查询服务

当前实现基于 orders 表的 status 字段提供物流摘要。
后续可扩展为对接外部物流 API。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.customer_service.errors import (
    DatabaseError,
    OrderNotFoundError,
)
from backend.customer_service.security.permission import PermissionChecker
from backend.shared.logger import logger


@dataclass
class LogisticsResult:
    order_id: str
    order_no: str
    status: str
    status_display: str
    estimated_delivery: Optional[str] = None
    tracking_info: Optional[str] = None
    trace_events: list = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.trace_events is None:
            self.trace_events = []


_STATUS_DISPLAY = {
    "pending": "待处理",
    "paid": "已支付，待发货",
    "shipped": "已发货，运输中",
    "completed": "已签收",
    "cancelled": "已取消",
}


def _estimate_delivery(status: str, stale_days: int) -> Optional[str]:
    """根据状态与停滞天数估算预计送达（演示口径）。"""
    if status == "completed":
        return None  # 已签收无需预估
    if status == "shipped":
        base = 3 + max(0, stale_days - 2)  # 停滞越久预估越晚
        eta = datetime.now(timezone.utc) + timedelta(days=base)
        return eta.strftime("%Y-%m-%d")
    if status == "paid":
        eta = datetime.now(timezone.utc) + timedelta(days=7)
        return eta.strftime("%Y-%m-%d") + "（预计发货后送达）"
    return None


class LogisticsService:

    def query_logistics(self, user_id: str, order_id: str) -> LogisticsResult:
        """查询订单物流状态。

        Args:
            user_id: 当前认证用户 ID
            order_id: 订单 ID 或订单号

        Raises:
            ValidationError: order_id 格式不合法
            OrderNotFoundError: 订单不存在或不属于该用户
            DatabaseError: 数据库异常
        """
        PermissionChecker.validate_order_id(order_id)

        from backend.customer_service.service.demo_mode import resolve_user_id

        user_id = resolve_user_id(user_id)
        from backend.sql.executor import execute_sql_struct

        sql = """
            SELECT id, order_no, status, created_at
            FROM "order".orders
            WHERE (id::text = %(order_id)s OR order_no = %(order_id)s)
              AND customer_id::text = %(user_id)s
        """
        result = execute_sql_struct(
            sql, params={"order_id": order_id, "user_id": str(user_id)}
        )

        if result.status not in ("success", "no_data"):
            raise DatabaseError(f"查询物流信息失败: {result.error}")

        if not result.rows:
            raise OrderNotFoundError(
                f"Order {order_id} not found for user {user_id}"
            )

        order = result.rows[0]
        status = order.get("status", "unknown")

        tracking_info, trace_events, estimated_delivery = self._resolve_tracking(
            str(order["order_no"]), status
        )

        return LogisticsResult(
            order_id=str(order["id"]),
            order_no=order["order_no"],
            status=status,
            status_display=_STATUS_DISPLAY.get(status, status),
            estimated_delivery=estimated_delivery,
            tracking_info=tracking_info,
            trace_events=trace_events,
        )

    @staticmethod
    def _resolve_tracking(
        order_no: str, status: str
    ) -> tuple[str, list[dict], Optional[str]]:
        """优先走轨迹 Provider（demo Mock / 未来真实 API），失败回退状态推导。

        Returns:
            (tracking_info 文本, 轨迹事件列表, 预计送达)
        """
        from backend.customer_service.service.logistics_trace import (
            TraceProviderError,
            get_trace_provider,
        )

        provider = get_trace_provider()
        if provider is not None:
            try:
                trace = provider.get_trace(order_no)
            except TraceProviderError as exc:
                # 演示降级路径：Provider 失败 → 状态推导摘要 + 降级说明
                logger.warning(
                    f"[LogisticsService] 轨迹 Provider 失败，回退状态推导: {exc}"
                )
                fallback = LogisticsService._build_tracking_info(status, {})
                return f"{fallback}\n（轨迹服务暂不可用：{exc}）", [], None

            if trace.events:
                lines = [f"[{e.time[:16].replace('T', ' ')}] {e.location} — {e.description}" for e in trace.events]
                latest = trace.latest_event
                stale = trace.stale_days()
                info = "物流轨迹（{}）:\n{}".format(trace.provider, "\n".join(lines))
                if stale >= 3:
                    info += f"\n⚠️ 包裹已停滞 {stale} 天无更新。"
                estimated = (
                    _estimate_delivery(status, stale) if latest else None
                )
                return info, [
                    {"time": e.time, "location": e.location, "description": e.description}
                    for e in trace.events
                ], estimated

        # 无 Provider 或空轨迹 → 原有状态推导
        return LogisticsService._build_tracking_info(status, {}), [], None

    @staticmethod
    def _build_tracking_info(status: str, order: dict) -> str:
        """根据订单状态生成物流摘要。"""
        if status == "shipped":
            return "您的包裹正在运输中，请耐心等待。"
        if status == "completed":
            return "您的包裹已签收。"
        if status == "paid":
            return "订单已支付，仓库正在准备发货。"
        if status == "pending":
            return "订单待处理，尚未进入物流环节。"
        if status == "cancelled":
            return "订单已取消，无物流信息。"
        return "暂无物流信息。"


_service_instance: LogisticsService | None = None
_service_lock = threading.Lock()


def get_logistics_service() -> LogisticsService:
    global _service_instance
    if _service_instance is None:
        with _service_lock:
            if _service_instance is None:
                _service_instance = LogisticsService()
    return _service_instance
