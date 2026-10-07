"""customer_service/service/account_service.py — 账户查询服务"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional

from backend.customer_service.errors import (
    AccountNotFoundError,
    AuthenticationError,
    DatabaseError,
)


@dataclass
class AccountResult:
    user_id: str
    name: Optional[str] = None
    gender: Optional[str] = None
    level: Optional[str] = None
    register_time: Optional[str] = None


class AccountService:

    def query_account(self, user_id: str) -> AccountResult:
        """查询用户账户信息。

        Args:
            user_id: 当前认证用户 ID

        Raises:
            AuthenticationError: user_id 缺失或 anonymous（身份层问题）
            AccountNotFoundError: 身份有效但客户表无此账户行（业务不存在）
            DatabaseError: 数据库异常
        """
        if not user_id or user_id == "anonymous":
            raise AuthenticationError("user_id is required")

        from backend.customer_service.service.demo_mode import resolve_user_id

        user_id = resolve_user_id(user_id)
        from backend.sql.executor import execute_sql_struct

        sql = """
            SELECT id, name, gender, level, register_time
            FROM customer.customers
            WHERE id::text = %(user_id)s
        """
        result = execute_sql_struct(sql, params={"user_id": str(user_id)})

        if result.status not in ("success", "no_data"):
            raise DatabaseError(f"查询账户信息失败: {result.error}")

        if not result.rows:
            # 2026-10-07 口径修正（收口审计 P2-7）：能走到这里说明身份已在
            # 入口校验通过，「客户表无此行」是业务不存在，不是「未认证」
            # ——旧映射 AuthenticationError 会把查无账户谎报成登录问题。
            raise AccountNotFoundError(
                f"Customer not found for user_id={user_id}"
            )

        row = result.rows[0]
        return AccountResult(
            user_id=str(row["id"]),
            name=row.get("name"),
            gender=row.get("gender"),
            level=row.get("level"),
            register_time=str(row["register_time"]) if row.get("register_time") else None,
        )


_service_instance: AccountService | None = None
_service_lock = threading.Lock()


def get_account_service() -> AccountService:
    global _service_instance
    if _service_instance is None:
        with _service_lock:
            if _service_instance is None:
                _service_instance = AccountService()
    return _service_instance
