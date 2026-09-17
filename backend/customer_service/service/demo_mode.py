"""customer_service/service/demo_mode.py — 演示业务沙盒模式

Demo Business Sandbox 的身份映射收口点。

机制：
- ``CS_DEMO_MODE=true`` 时，客服业务服务层把当前登录用户的 user_id
  映射为 ``CS_DEMO_CUSTOMER_ID``，使注册账号无需真实业务数据即可
  走完整客服流程（订单/物流/退款/售后）。
- 所有演示数据由 ``backend/sql/seeds/demo_sandbox.sql`` 播种，
  订单号统一 ``DEMO-`` 前缀，仅归属 demo customer。
- 关闭开关即回到真实身份与真实数据，不改任何权限模型。

设计见 docs/customer-service/演示沙盒方案-2026-09-17.md §三 SB-1。
"""
from __future__ import annotations

from typing import Any


def is_demo_mode() -> bool:
    """是否开启演示沙盒模式（运行时读取，便于测试与热切换）。"""
    from backend.config import customer_service as cs_config

    return bool(cs_config.CS_DEMO_MODE)


def resolve_user_id(user_id: Any) -> Any:
    """demo 模式下把业务查询身份映射为演示客户 ID。

    在各业务 Service 的 _get_order* 收口点调用（而非 Router/权限层），
    确保认证、权限校验仍使用真实身份，仅数据归属切换。
    """
    if not is_demo_mode():
        return user_id
    from backend.config import customer_service as cs_config

    return cs_config.CS_DEMO_CUSTOMER_ID
