"""travel/commerce/graph_state.py — Commerce 域图状态（STOP K6）

跨轮契约（AGENTS.md 旅游域教训，checkpointer 关闭也须遵守）：
1. ``new_commerce_graph_input()`` **只放本轮输入**，不预置产物/执行态——
   预置等于把上轮成果声明成本轮默认值；
2. 读状态一律 ``.get()``——本轮没写过的键不在最终状态里。

无 checkpointer（K0 §7 决策 1：单发查询无跨轮语义），TypedDict 仅为
LangGraph state schema 服务。
"""
from __future__ import annotations

from typing import Any, TypedDict


# 节点名（与 travel/graph_state.py 同风格常量）
COMMERCE_SLOT_FILLER = "travel_commerce_slot_filler"
COMMERCE_EXECUTOR = "travel_commerce_executor"


class CommerceGraphState(TypedDict, total=False):
    # ── 本轮输入（new_commerce_graph_input 独占写入）──
    user_message: str
    user_id: str
    session_id: str
    conversation_id: str
    tenant_id: str

    # ── slot_filler 产物 ──
    commerce_type: str            # hotel | flight
    commerce_request: dict        # 校验通过的请求参数（date 已转 ISO str）
    commerce_missing: list[str]   # 缺失必填槽位名
    commerce_clarification: str   # 缺槽位时的追问文案（executor 直通渲染）

    # ── executor 产物 ──
    final_answer: str
    commerce_status: str          # success|empty|disabled|... （trace/观测）
    commerce_offer_count: int


def new_commerce_graph_input(
    *, user_message: str, user_id: str = "", session_id: str = "",
    conversation_id: str = "", tenant_id: str = "",
) -> dict[str, Any]:
    """主图 state → commerce 图输入（只带本轮输入，零产物预置）。"""
    return {
        "user_message": user_message or "",
        "user_id": user_id or "",
        "session_id": session_id or "",
        "conversation_id": conversation_id or "",
        "tenant_id": tenant_id or "",
    }
