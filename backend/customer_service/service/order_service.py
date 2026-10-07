"""customer_service/service/order_service.py — 订单查询服务

批次B（2026-09-22）：业务网关双模式收口。
  - sandbox（默认）：直查内部演示库 + demo 身份映射，历史行为不变
  - http：经 business_client 调 business-service（Java / mock 容器），
    契约见 backend/mock/business_service/contract.md

模式判定收口在 service 层（与 demo_mode 同位），Router/权限层零改动。
http 模式失败映射回既存异常类型（OrderNotFoundError/DatabaseError），
调用方（query_expert 等）的错误处理路径无需感知模式。
网关失败**不降级回 sandbox**——真环境返回假数据比查不到更危险。

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


def _use_http_gateway() -> bool:
    from backend.config.customer_service import CS_BUSINESS_GATEWAY_MODE

    return CS_BUSINESS_GATEWAY_MODE == "http"


def _unwrap(resp: dict) -> dict:
    """解统一响应封套 {"code", "message", "data"}；code!=0 视为业务失败。"""
    code = resp.get("code", 0)
    if code != 0:
        raise DatabaseError(f"业务网关返回失败 code={code}: {resp.get('message')}")
    data = resp.get("data")
    if not isinstance(data, dict):
        raise DatabaseError("业务网关响应缺少 data 字段")
    return data


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
        if _use_http_gateway():
            return self._http_query_orders(user_id, order_id, query_type)

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
        if _use_http_gateway():
            return self._http_query_order_items(user_id, order_id)

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

    # ── http 网关模式（批次B）─────────────────────────────

    def _http_query_orders(
        self,
        user_id: str,
        order_id: str | None,
        query_type: str,
    ) -> OrderQueryResult:
        if query_type == "detail":
            if not order_id:
                raise ValidationError("detail 查询需要提供 order_id")
            PermissionChecker.validate_order_id(order_id)
            return OrderQueryResult(
                orders=[self._http_get_single_order(user_id, order_id)],
                total_count=1,
                query_type="detail",
            )
        data = self._http_get("/business/orders", user_id)
        orders = data.get("orders")
        if not isinstance(orders, list):
            raise DatabaseError("业务网关订单列表响应格式异常")
        return OrderQueryResult(
            orders=orders, total_count=len(orders), query_type="list",
        )

    def _http_query_order_items(self, user_id: str, order_id: str) -> list[dict]:
        PermissionChecker.validate_order_id(order_id)
        data = self._http_get(f"/business/orders/{order_no_urlsafe(order_id)}", user_id)
        order = data.get("order") or {}
        items = order.get("items")
        if not isinstance(items, list):
            # 真实 Java 侧未提供明细端点前，列表契约不含 items —— 显式报错
            raise DatabaseError("业务网关暂未提供订单明细数据")
        return items

    def _http_get_single_order(self, user_id: str, order_id: str) -> dict:
        data = self._http_get(
            f"/business/orders/{order_no_urlsafe(order_id)}", user_id,
        )
        order = data.get("order")
        if not isinstance(order, dict):
            raise DatabaseError("业务网关订单详情响应格式异常")
        return order

    def _http_get(self, path: str, user_id: str) -> dict:
        from backend.customer_service.service.demo_mode import resolve_user_id
        from backend.infra.http.business_client import (
            BusinessServiceError,
            get_json_sync,
        )

        try:
            resp = get_json_sync(path, params={"user_id": str(resolve_user_id(user_id))})
        except BusinessServiceError as exc:
            if exc.status_code == 404:
                raise OrderNotFoundError(f"Order not found via gateway: {path}")
            # 网关挂了/超时/5xx：显式失败，绝不降级回演示库假数据
            raise DatabaseError(f"业务网关不可用: {exc}") from exc
        return _unwrap(resp)

    # ── sandbox 模式（直查演示库，历史行为不变）──────────

    def _get_single_order(self, user_id: str, order_id: str) -> dict:
        """获取单个订单并验证归属。"""
        from backend.customer_service.service.demo_mode import resolve_user_id

        user_id = resolve_user_id(user_id)
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
        from backend.customer_service.service.demo_mode import resolve_user_id

        user_id = resolve_user_id(user_id)
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

    def list_refundable_orders(
        self, user_id: str, window_days: int = 7, limit: int = 5,
    ) -> list[dict]:
        """政策窗口内的可退订单候选（退款点选数据源，2026-10-08 拍板）。

        条件：状态可退（paid/shipped/completed）+ 下单时间在政策窗口内 +
        尚无退款记录（防重复）；联 order_items→products 取商品名（缺商品
        名时返回空串，由上层结合政策话术说明）。sandbox 直查；http 网关
        模式暂不支持候选查询（返回空，上层退回手输订单号路径）。
        """
        if _use_http_gateway():
            return []

        from backend.customer_service.service.demo_mode import resolve_user_id

        user_id = resolve_user_id(user_id)
        from backend.sql.executor import execute_sql_struct

        sql = """
            SELECT o.id, o.order_no, o.total_amount, o.status, o.created_at,
                   COALESCE((
                       SELECT string_agg(DISTINCT p.product_name, '、')
                       FROM "order".order_items oi
                       LEFT JOIN product.products p ON p.id = oi.product_id
                       WHERE oi.order_id = o.id
                   ), '') AS product_names
            FROM "order".orders o
            WHERE o.customer_id::text = %(user_id)s
              AND o.status IN ('paid', 'shipped', 'completed')
              AND o.created_at >= NOW() - (%(days)s * INTERVAL '1 day')
              AND NOT EXISTS (
                  SELECT 1 FROM "order".refunds r WHERE r.order_id = o.id
              )
            ORDER BY o.created_at DESC
            LIMIT %(limit)s
        """
        result = execute_sql_struct(
            sql, params={"user_id": str(user_id), "days": int(window_days), "limit": int(limit)},
        )
        if result.status not in ("success", "no_data"):
            raise DatabaseError(f"查询退款候选订单失败: {result.error}")
        return list(result.rows or [])


def order_no_urlsafe(order_id: str) -> str:
    """路径段转义：order_id 已由 PermissionChecker 校验为安全字符，
    此处再兜底去掉斜杠/空白，防路径拼接意外。"""
    return str(order_id).strip().replace("/", "")


_service_instance: OrderService | None = None
_service_lock = threading.Lock()


def get_order_service() -> OrderService:
    global _service_instance
    if _service_instance is None:
        with _service_lock:
            if _service_instance is None:
                _service_instance = OrderService()
    return _service_instance
