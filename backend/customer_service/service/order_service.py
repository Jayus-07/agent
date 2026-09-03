"""customer_service/service/order_service.py — 订单查询服务

设计参考: docs/customer-service/design.md §5.3
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

from backend.customer_service.errors import (
    DatabaseError,
    OrderNotFoundError,
    ValidationError,
)
from backend.customer_service.security.permission import PermissionChecker

_ORDER_ID_RE = re.compile(r"^[a-zA-Z0-9\-]+$")


@dataclass
class OrderQueryResult:
    orders: list[dict] = field(default_factory=list)
    total_count: int = 0
    query_type: str = "list"


class OrderService:

    def query_orders(
        self,
        user_id: str,
        order_id: str | None = None,
        query_type: str = "list",
    ) -> OrderQueryResult:
        """查询订单。

        Args:
            user_id: 当前认证用户 ID (必须)
            order_id: 指定订单 ID (detail 模式必须)
            query_type: "list" | "detail"

        Raises:
            ValidationError: order_id 格式不合法
            OrderNotFoundError: 订单不存在或不属于该用户
            DatabaseError: 数据库异常
        """
        if query_type == "detail":
            if not order_id:
                raise ValidationError("detail 查询需要提供 order_id")
            PermissionChecker.validate_order_id(order_id)
            return OrderQueryResult(
                orders=[self._get_single_order(user_id, order_id)],
                total_count=1,
                query_type="detail",
            )

        return self._get_order_list(user_id)

    def query_order_items(self, user_id: str, order_id: str) -> list[dict]:
        """查询订单明细。先验证订单归属。"""
        PermissionChecker.validate_order_id(order_id)
        order = self._get_single_order(user_id, order_id)

        from backend.sql.executor import execute_sql_struct

        sql = """
            SELECT oi.id, oi.product_id, oi.quantity, oi.price
            FROM "order".order_items oi
            WHERE oi.order_id = %(order_id)s
            ORDER BY oi.id
        """
        result = execute_sql_struct(sql, params={"order_id": order["id"]})

        if result.status not in ("success", "no_data"):
            raise DatabaseError(f"查询订单明细失败: {result.error}")

        return result.rows

    def _get_single_order(self, user_id: str, order_id: str) -> dict:
        """获取单个订单并验证归属。"""
        from backend.sql.executor import execute_sql_struct

        sql = """
            SELECT id, order_no, customer_id, total_amount, status,
                   payment_status, created_at
            FROM "order".orders
            WHERE (id::text = %(order_id)s OR order_no = %(order_id)s)
              AND customer_id::text = %(user_id)s
        """
        result = execute_sql_struct(
            sql, params={"order_id": order_id, "user_id": str(user_id)}
        )

        if result.status not in ("success", "no_data"):
            raise DatabaseError(f"查询订单失败: {result.error}")

        if not result.rows:
            raise OrderNotFoundError(
                f"Order {order_id} not found for user {user_id}"
            )

        return result.rows[0]

    def _get_order_list(self, user_id: str) -> OrderQueryResult:
        """获取用户订单列表。"""
        from backend.sql.executor import execute_sql_struct

        sql = """
            SELECT id, order_no, total_amount, status, payment_status, created_at
            FROM "order".orders
            WHERE customer_id::text = %(user_id)s
            ORDER BY created_at DESC
            LIMIT 20
        """
        result = execute_sql_struct(sql, params={"user_id": str(user_id)})

        if result.status not in ("success", "no_data"):
            raise DatabaseError(f"查询订单列表失败: {result.error}")

        return OrderQueryResult(
            orders=result.rows,
            total_count=len(result.rows),
            query_type="list",
        )


_service_instance: OrderService | None = None
_service_lock = threading.Lock()


def get_order_service() -> OrderService:
    global _service_instance
    if _service_instance is None:
        with _service_lock:
            if _service_instance is None:
                _service_instance = OrderService()
    return _service_instance
