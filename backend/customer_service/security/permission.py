"""customer_service/security/permission.py — 权限校验层

身份验证 + 资源归属校验，确保用户只能访问自己的数据。
设计参考: docs/customer-service/design.md §6
"""
from __future__ import annotations

import re

from backend.customer_service.errors import (
    AuthenticationError,
    AuthorizationError,
    ValidationError,
)
from backend.shared.logger import logger

_ORDER_ID_RE = re.compile(r"^[a-zA-Z0-9\-]+$")


class PermissionChecker:

    @staticmethod
    def validate_user_identity(state: dict) -> str:
        """从 state.cs_context 提取 authenticated_user_id。

        Returns:
            user_id (str)

        Raises:
            AuthenticationError: 缺失或 anonymous
        """
        cs_context = state.get("cs_context", {})
        user_id = cs_context.get("authenticated_user_id")
        if not user_id or user_id == "anonymous":
            logger.warning(
                f"[Permission] 身份验证失败: user_id={user_id!r}"
            )
            raise AuthenticationError(
                "user_id missing or anonymous in cs_context"
            )
        return user_id

    @staticmethod
    def validate_order_id(order_id: str) -> str:
        """校验 order_id 格式。

        Raises:
            ValidationError: 格式不合法
        """
        if not order_id:
            raise ValidationError("order_id 不能为空")
        if not _ORDER_ID_RE.match(order_id) or len(order_id) > 64:
            raise ValidationError(
                f"order_id 格式不合法: {order_id!r}"
            )
        return order_id

    @staticmethod
    def check_order_access(user_id: str, order: dict) -> dict:
        """验证订单归属权。

        Args:
            user_id: 当前认证用户
            order: 订单数据 (须含 customer_id 字段)

        Raises:
            AuthorizationError: 订单不属于该用户
        """
        order_customer = str(order.get("customer_id", ""))
        if order_customer != str(user_id):
            logger.warning(
                f"[Permission] 越权访问: user_id={user_id} "
                f"order_customer_id={order_customer}"
            )
            raise AuthorizationError(
                f"User {user_id} cannot access order owned by {order_customer}"
            )
        return order
