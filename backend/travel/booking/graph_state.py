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

    # ── resolver 产物 ──
    booking_action: str          # new | confirm | status | unknown
    booking_params: dict         # commerce_type/search_params/selection
    booking_clarification: str

    # ── executor 产物 ──
    final_answer: str
    booking_status: str          # quoted|confirmed|booked|in_doubt|... |clarify


def new_booking_graph_input(*, user_message: str, user_id: str = "",
                            session_id: str = "", tenant_id: str = "") -> dict[str, Any]:
    return {
        "user_message": user_message or "",
        "user_id": user_id or "",
        "session_id": session_id or "",
        "tenant_id": tenant_id or "",
    }
