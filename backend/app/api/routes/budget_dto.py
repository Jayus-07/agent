"""预算 API DTO 的纯转换函数。

金额在服务端始终以六位小数字符串返回；浏览器只负责展示，不参与结算。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


_SIX_PLACES = Decimal("0.000001")


def _decimal_text(value: Decimal | int | float | str) -> str:
    return str(Decimal(str(value)).quantize(_SIX_PLACES, rounding=ROUND_HALF_UP))


def build_budget_window(
    *,
    used: Decimal,
    reserved: Decimal,
    limit: Decimal,
    reset_at: datetime,
) -> dict[str, Any]:
    """构造前端窗口 DTO，保留金额精度并提供展示用比例。"""
    total = used + reserved
    ratio = float(total / limit) if limit > 0 else 0.0
    return {
        "used": _decimal_text(used),
        "reserved": _decimal_text(reserved),
        "limit": _decimal_text(limit),
        "ratio": ratio,
        "reset_at": reset_at.isoformat(),
    }


def budget_details(
    scope_type: str,
    period_type: str,
    reset_at: str,
) -> dict[str, str]:
    """构造 BUDGET_EXCEEDED 的稳定 details，不透传内部异常。"""
    normalized_period = "monthly" if period_type == "month" else "daily"
    return {
        "budget_kind": scope_type,
        "limit_kind": normalized_period,
        "scope_type": scope_type,
        "period_type": period_type,
        "reset_at": reset_at,
    }
