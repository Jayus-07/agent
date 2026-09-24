"""travel/booking/state.py — BookingOrder 状态机（STOP L3，G20/G21/G22）

**集中唯一转换闸**：禁止散落 `order.status = "BOOKED"`。所有转换必须经
`transition()`：白名单校验 + 状态版本单调递增 + 失败原因登记；非法转换
fail-closed 抛 `IllegalBookingTransition`。

单调性（§二十）：终态（booked/failed/expired）不可逆；唯一非终态出口是
in_doubt → booked / failed，且只允许 cause=reconciliation|manual（对账或
人工裁决），业务 retry 不得把 IN_DOUBT 拉回 submitting（§二十四：不确定
结果禁止盲目重试）。
"""
from __future__ import annotations

from enum import Enum


class BookingOrderStatus(str, Enum):
    QUOTED = "quoted"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED = "confirmed"
    SUBMITTING = "submitting"
    BOOKED = "booked"
    FAILED = "failed"
    IN_DOUBT = "in_doubt"
    EXPIRED = "expired"


# 转换白名单（§十八；不加 RECONCILING——对账是 in_doubt 上的动作，不是状态）
ALLOWED_TRANSITIONS: dict[BookingOrderStatus, frozenset[BookingOrderStatus]] = {
    BookingOrderStatus.QUOTED: frozenset({
        BookingOrderStatus.AWAITING_CONFIRMATION,
        BookingOrderStatus.EXPIRED,
    }),
    BookingOrderStatus.AWAITING_CONFIRMATION: frozenset({
        BookingOrderStatus.CONFIRMED,
        BookingOrderStatus.EXPIRED,
    }),
    BookingOrderStatus.CONFIRMED: frozenset({
        BookingOrderStatus.SUBMITTING,
        BookingOrderStatus.FAILED,      # 确认后价格/库存变化阻断（B11/B12，create=0）
        BookingOrderStatus.EXPIRED,     # 确认过期
    }),
    BookingOrderStatus.SUBMITTING: frozenset({
        BookingOrderStatus.BOOKED,
        BookingOrderStatus.FAILED,      # NOT_SENT（明确未过界，可重入）或 REJECTED
        BookingOrderStatus.IN_DOUBT,    # 结果未知（§二十四）
    }),
    BookingOrderStatus.IN_DOUBT: frozenset({
        BookingOrderStatus.BOOKED,      # 仅 reconciliation/manual 证明成功
        BookingOrderStatus.FAILED,      # 仅 reconciliation/manual 证明未发生
    }),
    # 终态
    BookingOrderStatus.BOOKED: frozenset(),
    BookingOrderStatus.FAILED: frozenset(),
    BookingOrderStatus.EXPIRED: frozenset(),
}

# 允许把订单推进到该状态的动作来源（§二十四：IN_DOUBT 出口仅两类 cause）
_IN_DOUBT_EXIT_CAUSES = frozenset({"reconciliation", "manual"})


class IllegalBookingTransition(ValueError):
    """非法状态转换（fail-closed）：调用方必须中止，不得绕过状态机。"""


def can_transition(current: BookingOrderStatus, target: BookingOrderStatus,
                   cause: str = "") -> bool:
    """转换合法性（纯函数）。IN_DOUBT 出口额外校验 cause 白名单。"""
    if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
        return False
    if (current is BookingOrderStatus.IN_DOUBT
            and cause not in _IN_DOUBT_EXIT_CAUSES):
        return False
    return True


def require_transition(current: BookingOrderStatus, target: BookingOrderStatus,
                       cause: str = "") -> None:
    """非法即抛（fail-closed）。"""
    if not can_transition(current, target, cause):
        raise IllegalBookingTransition(
            f"非法状态转换: {current.value} → {target.value}"
            + (f"（cause={cause}）" if cause else ""))
