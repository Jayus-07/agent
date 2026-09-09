"""customer_service/service/logistics_service.py — 物流查询服务

当前实现基于 orders 表的 status 字段提供物流摘要。
后续可扩展为对接外部物流 API。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional

from backend.customer_service.errors import (
    DatabaseError,
    OrderNotFoundError,
)
from backend.customer_service.security.permission import PermissionChecker


@dataclass
class LogisticsResult:
    order_id: str
    order_no: str
    status: str
    status_display: str
    estimated_delivery: Optional[str] = None
    tracking_info: Optional[str] = None


_STATUS_DISPLAY = {
    "pending": "待处理",
    "paid": "已支付，待发货",
    "shipped": "已发货，运输中",
    "completed": "已签收",
    "cancelled": "已取消",
}


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

        return LogisticsResult(
            order_id=str(order["id"]),
            order_no=order["order_no"],
            status=status,
            status_display=_STATUS_DISPLAY.get(status, status),
            tracking_info=self._build_tracking_info(status, order),
        )

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
