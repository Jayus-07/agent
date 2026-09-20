"""客服派单域服务（P4 入池 / P6 自动派单 / P7 接单与回收 / P8 outbox）。

模块分工：

===============  ====================================================
``service``      P4 ``create_or_reuse_handoff``、P6 ``dispatch_once``
``repository``   派单/reaper/outbox 的 PostgreSQL 读写原语（强制租户）
``presence``     Redis 在线状态（只判断候选，不承担生命周期权威）
``offers``       P7 坐席 accept / decline 与主管 reassign
``reaper``       P7 过期 offer 回收与工单终态
``outbox``       事件落库（事务内）与持久投递（提交后）
``event_relay``  单次 fire-and-forget 广播（P6 路径，被 outbox 复用）
===============  ====================================================
"""

from backend.customer_service.dispatch.service import (
    OFFER_EVENT_TYPE,
    ConversationForbidden,
    ConversationNotFound,
    DispatchResult,
    HandoffConflict,
    HandoffResult,
    create_or_reuse_handoff,
    dispatch_once,
)

__all__ = [
    "OFFER_EVENT_TYPE",
    "ConversationForbidden",
    "ConversationNotFound",
    "DispatchResult",
    "HandoffConflict",
    "HandoffResult",
    "create_or_reuse_handoff",
    "dispatch_once",
]
