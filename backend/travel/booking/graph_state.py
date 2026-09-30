"""travel/booking/graph_state.py — Booking 域图状态（STOP L9）

跨轮契约（commerce 同款）：new_booking_graph_input 只放本轮输入；
读状态一律 .get()。无 checkpointer（订单事实在 PG，不依赖图状态）。
"""
from __future__ import annotations

from typing import Any, TypedDict

BOOKING_RESOLVER = "travel_booking_resolver"
BOOKING_EXECUTOR = "travel_booking_executor"


class BookingGraphState(TypedDict, total=False):
    # ── 本轮输入 ──
    user_message: str
    user_id: str
    session_id: str
    tenant_id: str
    # 上轮挂起（Phase 5 / D2）：主图适配器从 ConversationContext 读取后传入。
    # 子图无 checkpointer，跨轮参数不可能从图状态拿——这是唯一入口。
    # 只读；本图不修改它（回写挂起是适配器职责，图内零 IO 纪律）。
    pending_intent: dict

    # ── resolver 产物 ──
    booking_action: str          # new | confirm | status | unknown
    booking_params: dict         # commerce_type/search_params/selection
    booking_clarification: str
    # 挂起回写所需（Phase 5 / D2）：kind 供适配器写回挂起、missing 供下一轮
    # 判定「这句是不是在补这个槽」、collected 是已收槽位（值已 ISO 化）
    booking_kind: str
    booking_missing: list[str]
    booking_collected: dict

    # ── executor 产物 ──
    final_answer: str
    booking_status: str          # quoted|confirmed|booked|in_doubt|... |clarify


def new_booking_graph_input(*, user_message: str, user_id: str = "",
                            session_id: str = "", tenant_id: str = "",
                            pending_intent: dict | None = None) -> dict[str, Any]:
    """主图 state → booking 图输入（只带本轮输入，零产物预置）。

    ``pending_intent`` 为空 dict / None 时同样写入（resolver 一律 .get 读），
    与既有「读状态一律 .get()」契约一致。
    """
    return {
        "user_message": user_message or "",
        "user_id": user_id or "",
        "session_id": session_id or "",
        "tenant_id": tenant_id or "",
        "pending_intent": dict(pending_intent) if pending_intent else {},
    }
