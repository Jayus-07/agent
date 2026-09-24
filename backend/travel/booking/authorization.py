"""travel/booking/authorization.py — 授权与租户隔离（STOP L7，G31/G32）

规则（§三十五/§三十六）：
  - 所有执行/读取路径从服务端认证上下文重解析 tenant/user；
  - order/quote/intent 必须属主匹配，仅凭 id 一律拒绝（fail-closed）；
  - 前端提交的金额/币种/provider 字段一律不信任——事实从服务端
    Quote/Order 重读（本模块的 assert_* 即服务端事实入口）。
"""
from __future__ import annotations


class BookingAuthorizationError(PermissionError):
    """属主校验失败（403 语义）；调用方必须停止，不得降级放行。"""


def assert_owns_order(order: dict | None, *, tenant_id: str,
                      user_id: str) -> dict:
    if order is None:
        raise BookingAuthorizationError("订单不存在")
    if not tenant_id or not user_id:
        raise BookingAuthorizationError("缺少可信租户/用户上下文")
    if order["tenant_id"] != tenant_id or order["user_id"] != user_id:
        # 不区分「不存在」与「非本人」——避免探测
        raise BookingAuthorizationError("订单不存在")
    return order


def assert_owns_quote(quote: dict | None, *, tenant_id: str,
                      user_id: str) -> dict:
    if quote is None:
        raise BookingAuthorizationError("报价不存在")
    if not tenant_id or not user_id:
        raise BookingAuthorizationError("缺少可信租户/用户上下文")
    if quote["tenant_id"] != tenant_id or quote["user_id"] != user_id:
        raise BookingAuthorizationError("报价不存在")
    return quote
