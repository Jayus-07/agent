"""selection_funnel/graph_state.py — 选品漏斗域图状态

与 travel/graph_state.py 同一约定：
  - 状态里只放可 JSON 序列化的值（checkpointer 兼容）
  - new_*_graph_input 只放本轮输入，不预置产物默认值
  - 读取一律走 .get()（未写过的键不在状态里）
"""
from __future__ import annotations

from typing import Any, TypedDict

from backend.selection_funnel.models.brief import FunnelBrief

# ============================================================
# 节点名常量（graph_builder / 节点 / 适配器共用）
# ============================================================
FUNNEL_BRIEF = "funnel_brief"
FUNNEL_POOL = "funnel_pool"
FUNNEL_SCREEN = "funnel_screen"
FUNNEL_VERIFY = "funnel_verify"
FUNNEL_ECON = "funnel_econ"
FUNNEL_RANK = "funnel_rank"
FUNNEL_REPORT = "funnel_report"

# 漏斗层标识（stage_logs 与 reporter 共用）
STAGE_BRIEF = "brief"
STAGE_POOL = "pool"
STAGE_SCREEN = "screen"
STAGE_VERIFY = "verify"
STAGE_ECON = "econ"
STAGE_RANK = "rank"

# 终态口径（2026-09-17 四分：淘空不是故障，但系统异常也不能伪装成淘空）
STATUS_OK = "ok"                    # 漏斗跑完且有推荐
STATUS_NEED_INFO = "need_info"      # 缺关键槽位（类目）或需求条件非法，已生成追问
STATUS_EMPTY = "empty_pool"         # 某层把候选淘空（如实报告淘汰原因）
STATUS_FAILED = "failed"            # 节点异常/数据损坏等系统失败（适配器降级兜底并打观测标记，
                                    #   域图内节点不自行捕获——异常向上抛，由适配器统一 stamp）


class SelectionFunnelState(TypedDict, total=False):
    """选品漏斗域图状态

    **消费纪律：一切读取走 ``.get()``。**
    """

    # === 输入（由 selection_funnel_graph_node 适配器传入）===
    user_message: str
    user_id: str
    session_id: str
    conversation_id: str
    funnel_context: dict
    run_id: str                 # 本次运行唯一 ID（适配器生成 → config_snapshot/trace）

    # === 槽位 ===
    brief: dict
    brief_missing: list[str]
    clarifications: list[str]

    # === 漏斗产物 ===
    pool: list[dict]            # 建池结果（最新快照归一化 dict）
    candidates: list[dict]      # 当前存活的候选（逐层减少，字段逐层累加）
    stage_logs: list[dict]      # 每层 {stage, kept, dropped, reasons, notes}
    notes: list[str]            # 全局数据缺口/降级说明（reporter 如实披露）

    # === 执行态 ===
    status: str                 # STATUS_* 之一
    finished: bool
    config_snapshot: dict       # 本次运行阈值/口径快照（reporter 产出 → trace metadata）

    # === 输出 ===
    final_answer: str
    funnel_context: dict


def new_selection_funnel_graph_input(
    user_message: str,
    user_id: str = "",
    session_id: str = "",
    conversation_id: str = "",
    funnel_context: dict | None = None,
    run_id: str = "",
) -> dict[str, Any]:
    """构建漏斗域图输入 —— **只放本轮输入，不放任何产物或执行态的默认值**。

    与 travel 同一契约：checkpointer 开启时 input 会被当作上一轮状态的
    更新合并，预置默认值等于每轮清空成果。漏斗是一次性任务（P0 无跨轮
    改单诉求），这里仍保持同一纪律，未来开启 checkpointer 时零改动。
    """
    return {
        "user_message": user_message,
        "user_id": user_id,
        "session_id": session_id,
        "conversation_id": conversation_id,
        "funnel_context": funnel_context or {},
        "run_id": run_id,
    }


# ============================================================
# 契约转换（状态 dict ↔ Pydantic 模型）
# ============================================================
def load_brief(state: dict) -> FunnelBrief:
    raw = state.get("brief") or {}
    return FunnelBrief.model_validate(raw) if raw else FunnelBrief()


def save_brief(brief: FunnelBrief) -> dict:
    return brief.model_dump()
