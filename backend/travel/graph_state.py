"""travel/graph_state.py — 旅游域图独立状态

与 CS 域图同一约定：独立于 Main Graph 的 OrchestratorState，通过
travel_graph_node 适配器 + TravelGraphResult 契约与主图通信。

序列化纪律：状态里只放 **可 JSON 序列化** 的值。
  - brief / itinerary / validation 存 dict（Pydantic 模型在专家边界
    用 load_*/save_* 转换），这样开启 checkpointer 时状态可直接落库
  - 不放 tracer / stream sink 这类运行时对象（与主图 checkpoint_safe
    同一考量：能序列化的进状态，不能序列化的走 request_context）
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, TypedDict

from backend.travel.models.brief import TravelBrief
from backend.travel.models.itinerary import Itinerary
from backend.travel.models.validation import ValidationReport

# ============================================================
# 节点名常量（graph_builder / supervisor / 适配器共用，避免字符串漂移）
# ============================================================
TRAVEL_SLOT_FILLER = "travel_slot_filler"
TRAVEL_SUPERVISOR = "travel_supervisor"
TRAVEL_POI_EXPERT = "travel_poi_expert"
TRAVEL_TRANSIT_EXPERT = "travel_transit_expert"
TRAVEL_BUDGET_EXPERT = "travel_budget_expert"
TRAVEL_RISK_EXPERT = "travel_risk_expert"
TRAVEL_VALIDATOR = "travel_validator"
TRAVEL_REPAIR = "travel_repair"
TRAVEL_REPORTER = "travel_reporter"

# 专家节点名 ←→ 专家标识（supervisor 与 graph_builder 的单一映射源）
EXPERT_TO_NODE: dict[str, str] = {
    "poi": TRAVEL_POI_EXPERT,
    "transit": TRAVEL_TRANSIT_EXPERT,
    "budget": TRAVEL_BUDGET_EXPERT,
    "risk": TRAVEL_RISK_EXPERT,
}


class TravelGraphState(TypedDict, total=False):
    """旅游域图状态

    **消费纪律：一切读取走 ``.get()``。**
    域图输入（``new_travel_graph_input``）只放本轮输入、不预置任何默认值 ——
    预置会覆盖 checkpointer 的跨轮状态。因此「本轮没被写过的键」不会出现在
    最终状态里，任何 ``state["k"]`` 的硬取都可能 KeyError。
    """

    # === 输入（由 travel_graph_node 适配器传入）===
    user_message: str
    user_id: str
    session_id: str
    conversation_id: str
    travel_route: dict

    # === 槽位 ===
    brief: dict
    brief_missing: list[str]
    clarifications: list[str]
    # 上一轮 brief 的指纹：跨轮（checkpointer 开启）时用来判断需求是否变化，
    # 变了就清空规划产物重排，避免拿新约束贴旧行程
    brief_fingerprint: str
    # 需求变化的原因与差异字段（slot_filler 检测到指纹变化时写，transit expert
    # 构造行程时读取盖版本章；不进 planning_reset 清单——变化当轮产生当轮消费）
    brief_change_reason: str
    brief_changed_fields: list[str]

    # === 规划产物 ===
    candidates: list[dict]
    day_plan: list[list[str]]
    itinerary: dict | None
    validation: dict | None
    repair_rounds: int
    # 「本次违反无自动修复手段」的终态标记（repair 节点写、supervisor 读）。
    # 与 repair_rounds 分工不同：轮数是「试了几次」，本标记是「试也没用」。
    # 缺了它，repair 会被反复调起（同一状态必得同一决策）直到撞 recursion_limit。
    repair_stalled: bool
    repair_log: list[dict]
    # 防震荡签名（Phase 3，任务书 §6）：上一轮修复前的违反集签名 + 连续
    # 无改善轮数。repair 节点跨轮比对 —— 连续两轮违反集不变即停止重试。
    last_repair_constraint_sig: str
    repair_no_improvement_streak: int
    notes: list[str]

    # === 执行态 ===
    stage: str
    step_count: int
    current_expert: str
    expert_history: list[dict]
    last_expert_result: dict
    supervisor_decision: dict
    # 持久化状态（任务书 §10，Phase 4）：healthy / degraded / disabled。
    # slot_filler（图入口）从 graph_builder 单例读取后写进 state ——
    # supervisor_decision 携带进 trace，reporter 据此向用户披露降级事实。
    persistence_status: str
    # 候选方案容器（任务书 §14，Phase 7 预留）：P0 单方案产出，无节点写入，
    # 仅占位——将来「出 A/B 两版让用户挑」时由规划层填充 CandidatePlan 快照。
    candidate_plans: list[dict]

    # === 输出 ===
    final_answer: str
    travel_context: dict
    finished: bool


def new_travel_graph_input(
    user_message: str,
    user_id: str = "",
    session_id: str = "",
    conversation_id: str = "",
    travel_route: dict | None = None,
) -> dict[str, Any]:
    """构建旅游域图输入 —— **只放本轮输入，不放任何产物或执行态的默认值**。

    这不是省略，是必须遵守的契约。checkpointer 开启后，LangGraph 会把本次
    input 当作对「上一轮持久化状态」的**更新**合并进去；若这里带上
    ``brief: {} / itinerary: None / expert_history: []`` 这类默认值，就等于
    每轮都把上一轮的成果清空 —— 实测表现是第二轮槽位全丢（destination=''）、
    行程从头重排，跨轮改单完全失效。

    产物与执行态一律由节点自己填：首轮天然为空（各节点都用 ``.get`` 兜底），
    后续轮次则由状态自然延续。需要显式清空时走 ``planning_reset()``。
    """
    return {
        "user_message": user_message,
        "user_id": user_id,
        "session_id": session_id,
        "conversation_id": conversation_id,
        "travel_route": travel_route or {},
    }


# ============================================================
# 契约转换（状态 dict ↔ Pydantic 模型）
# ============================================================
def load_brief(state: dict) -> TravelBrief:
    """从状态取需求契约；缺失时返回空 brief（missing_slots 会全部报缺）。"""
    raw = state.get("brief") or {}
    return TravelBrief.model_validate(raw) if raw else TravelBrief()


def save_brief(brief: TravelBrief) -> dict:
    return brief.model_dump()


def load_itinerary(state: dict) -> Itinerary | None:
    raw = state.get("itinerary")
    return Itinerary.model_validate(raw) if raw else None


def save_itinerary(itinerary: Itinerary) -> dict:
    return itinerary.model_dump()


def load_validation(state: dict) -> ValidationReport | None:
    raw = state.get("validation")
    return ValidationReport.model_validate(raw) if raw else None


def save_validation(report: ValidationReport) -> dict:
    return report.model_dump()


def build_travel_context(state: dict) -> dict:
    """回传主图的上下文快照（不含大对象，只带可展示的摘要）。"""
    validation = load_validation(state)
    itinerary = load_itinerary(state)
    return {
        "brief": state.get("brief", {}),
        "brief_missing": state.get("brief_missing", []),
        "stage": state.get("stage", ""),
        "repair_rounds": state.get("repair_rounds", 0),
        "repair_log": state.get("repair_log", []),
        "validation_codes": validation.codes() if validation else [],
        "validation_passed": validation.passed if validation else None,
        "day_count": len(itinerary.days) if itinerary else 0,
        "total_cost_cny": itinerary.cost.total if itinerary else 0.0,
        "persistence_status": state.get("persistence_status", ""),
        "notes": state.get("notes", []),
    }


# ============================================================
# 需求指纹与重规划失效
# ============================================================
def brief_fingerprint(brief: TravelBrief) -> str:
    """对「会影响排程结果」的字段取指纹。

    只纳入真正改变行程的字段（目的地/天数/人数/预算/偏好/必去/避雷/节奏/日期），
    用户在消息里多说一句无关的话不该导致重排。
    """
    payload = {
        "destination": brief.destination,
        "days": brief.days,
        "start_date": brief.start_date.isoformat() if brief.start_date else None,
        "party_size": brief.party_size,
        "budget_cny": brief.budget_cny,
        "preferences": sorted(brief.preferences),
        "must_go": sorted(brief.must_go),
        "avoid": sorted(brief.avoid),
        "pace": brief.normalized_pace(),
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def data_snapshot_version(candidates: list[dict]) -> str:
    """候选池数据快照签名（任务书 §4 三层版本之一）。

    回答「这版行程基于哪份数据」：poi_id + source 的有序哈希 —— 同一 POI
    换了数据源（seed:local → tencent:lbs）即视为数据版本变化。候选池是
    排程的完整输入投影，签名稳定且可复现。
    """
    if not candidates:
        return ""
    payload = sorted(
        f"{c.get('poi_id', '')}|{c.get('source', '')}" for c in candidates
    )
    blob = "\n".join(payload)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:8]


def planning_reset() -> dict:
    """清空全部规划产物与执行态（保留 brief 与槽位结果）。

    **为什么必须有**：开启 checkpointer 后状态跨轮保留，第二轮若只在
    slot_filler 里更新 brief 而不清旧产物，supervisor 会看到
    「专家都跑过 + 有一份通过的 validation」→ 直接进 reporter，
    于是把**上一轮的行程**当成新需求的结果输出（拿新约束贴旧计划）。
    这比不持久化更糟：输出看着完整，却与用户刚说的要求不符。

    阈值调大 / 加天数 / 换目的地同理，一律重规划 —— 确定性重排只要百毫秒级，
    用「宁可重算」换「绝不输出与需求不符的行程」，这个取舍不用犹豫。
    """
    return {
        "candidates": [],
        "day_plan": [],
        "itinerary": None,
        "validation": None,
        "repair_rounds": 0,
        "repair_stalled": False,
        "repair_log": [],
        "last_repair_constraint_sig": "",
        "repair_no_improvement_streak": 0,
        "expert_history": [],
        "last_expert_result": {},
        "supervisor_decision": {},
        "step_count": 0,
        "current_expert": "",
        "stage": "",
        "finished": False,
        "notes": [],
        "candidate_plans": [],
    }
