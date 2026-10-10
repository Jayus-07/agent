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
TRAVEL_WEATHER_EXPERT = "travel_weather_expert"
TRAVEL_BUDGET_EXPERT = "travel_budget_expert"
TRAVEL_RISK_EXPERT = "travel_risk_expert"
TRAVEL_VALIDATOR = "travel_validator"
TRAVEL_REPAIR = "travel_repair"
TRAVEL_PARTIAL_REPLAN = "travel_partial_replan"
TRAVEL_AUXILIARY_TASKS = "travel_auxiliary_tasks"
TRAVEL_REPORTER = "travel_reporter"

# 专家节点名 ←→ 专家标识（supervisor 与 graph_builder 的单一映射源）
EXPERT_TO_NODE: dict[str, str] = {
    "poi": TRAVEL_POI_EXPERT,
    "transit": TRAVEL_TRANSIT_EXPERT,
    "weather": TRAVEL_WEATHER_EXPERT,
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
    # 本轮统一请求契约；每轮显式覆盖，避免 Checkpointer 留下上轮 UI/动作状态。
    request_mode: str
    # read_only 问答只从版本账本注入 Active/Draft 事实，且运行于无 checkpoint 图。
    read_only_context: dict
    brief_input: dict | None
    base_plan_version: int | None
    ui_context: dict
    action_payload: dict
    turn_id: str
    # 本轮单一需求决策；旧 intent 是供存量节点消费的兼容投影。
    turn_decision: dict
    turn_decision_meta: dict
    # graceful reconstruction（STOP F3）：checkpoint 缺失但会话摘要存在时，
    # 适配器从 ConversationContext 重建的 brief 基底。只在 thread 无
    # checkpoint 时出现；slot_filler 以它为 previous 合并本轮消息。
    # 它属于「本轮输入」（由本轮适配器构造），不违反 new_travel_graph_input
    # 的「不预置产物」契约——它不是产物，是恢复的事实基底。
    reconstruct_brief: dict

    # === 槽位 ===
    brief: dict
    brief_missing: list[str]
    clarifications: list[str]
    clarification_options: list[dict]
    # 追问单一生成点（2026-10-08 STOP 3/5）：slot_filler 产出的追问计划与
    # 渲染来源（llm|template，空=无追问）。reporter 只消费 clarifications，
    # 不再二次生成。必须入 schema——LangGraph 会剥离 schema 外的更新键。
    clarification_plan: dict
    clarification_source: str
    clarification_meta: dict
    # LLM 槽位富化观测（2026-10-08 STOP 1）：slot_parse_source ∈
    # rule | rule+llm | rule_fallback；slot_llm_meta = 调用结局投影
    # （used/status/model/prompt_version/latency_ms/candidate/accepted/
    # rejected/fallback_reason，全标量）。只是观测投影——LLM 在结构上
    # 不写任何业务状态。
    slot_parse_source: str
    slot_llm_meta: dict
    # 上一轮 brief 的指纹：跨轮（checkpointer 开启）时用来判断需求是否变化，
    # 变了就清空规划产物重排，避免拿新约束贴旧行程
    brief_fingerprint: str
    # 需求变化的原因与差异字段（slot_filler 检测到指纹变化时写，transit expert
    # 构造行程时读取盖版本章；不进 planning_reset 清单——变化当轮产生当轮消费）
    brief_change_reason: str
    brief_changed_fields: list[str]
    # 本轮重规划前的产物版本。planning_reset 会清掉 itinerary，但版本链
    # 不能因此回到 v1；transit expert 用它为新产物盖 parent + 1。
    plan_parent_version: int | None
    # 需求变更前的可序列化快照，用于局部稳定性计算与预算-only 保留 POI。
    plan_stability_baseline: dict
    preserved_days: list[int]
    changed_days: list[int]
    scope_expansion_reason: str

    # === 规划产物 ===
    candidates: list[dict]
    day_plan: list[list[str]]
    # must_go 三态契约（STOP I1）：poi 专家解析出的「候选池确认没有」的
    # 点名地点——如实披露与金标 Q2 的数据源，validator coverage 仍以
    # brief.must_go × 行程为准（此处只承载「数据层不存在」这一事实）。
    must_go_unresolved: list[str]
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
    # 知识库摘录（P0-1）：risk expert 检索 RAG 后写入，reporter 渲染为
    # 「知识库参考」段。与 notes 分开：notes 是「行程被迫做的取舍」，
    # 摘录是「可引用的外部知识」，语义不同不应混排。
    knowledge_refs: list[str]
    # 统一证据表（travel-domain-design-v5.md §13，Phase 4）：fact_id → Evidence dict。模型唯一归属
    # core/contracts.py，本文件只声明 state 键。纪律：不进
    # new_travel_graph_input 预置（跨轮契约 1）；读取一律 .get()（跨轮契约
    # 2——旧 checkpoint 无此键 = 无证据，SOURCE_STALE 不误报）；写入走
    # {**state.get("evidences", {}), **新证据} 合并（无 reducer 键是覆盖
    # 语义，直接写会冲掉前序节点证据）。纯 dict 可序列化。
    evidences: dict[str, dict]
    # 用户明确要求实时查美食/酒店/车票时的真实 Tool 结果摘要；只存可序列化
    # 的展示数据，不把上游响应对象塞进 checkpoint。
    live_search: dict[str, dict]
    # 本轮与主规划正交的 Tool 结果；每轮由 slot_filler 重置，供 API/Reporter 消费。
    task_results: list[dict]
    # 会话意图（backend/travel/core/intent.py，P0-A）：slot_filler 每轮分类后写入（空串 = 未分类，
    # 走既有规划链）；supervisor 意图先行门禁与 reporter 问答出口消费。
    # 必须入 schema——LangGraph updates 会剥离 schema 外的键。
    intent: str
    # 查询消息中的城市主题；仅用于轻量回答，不属于 TripBrief 目的地。
    query_destination: str
    # 槽位事实来源：explicit / default / inferred / missing，供 Trace 与
    # 前端解释「未说人数为何按 1 人」；不参与业务指纹。
    slot_sources: dict[str, str]
    destination_change: bool
    # QUERY_STATIC 意图的灵感包（services/inspiration_service 产出）：
    # {destination, guides: [{title,url,summary,author,source}], status}。
    # 增强信息：检索失败不阻塞任何链路，reporter 按 status 三态渲染。
    inspiration: dict
    # QUERY_TRANSIT 意图的车票查询包（由 travel_auxiliary_tasks 写入）：
    # {status: ok|failed|missing_origin|missing_destination, origin,
    #  destination, date, trains: [...], source}。与 inspiration 同口径：
    # 预取失败不阻塞（status 如实呈现），reporter 消费渲染。
    transit_query: dict

    # === 执行态 ===
    # 本轮局部改单请求与确定性执行结果；dict 形态保证可进入 checkpoint。
    partial_replan: dict
    partial_replan_done: bool
    partial_replan_result: dict
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
    # Tool Failure Policy 结局记录（2026-10-07 容错改造）。契约冻结：
    # Tool Failure ≠ Workflow Failure——degraded_tools 是「实时数据未验证
    # 但行程继续」的披露账（reporter 渲染 ⚠ 段、trace.travel_* 打标）；
    # blocked_tools 是「硬依赖无法验证、Workflow 终止」的事实（supervisor
    # 据此 REPORT 终止，reporter 给用户可读原因）；tool_failures 是原始
    # 失败账（error_code/retry_count 等，管理端/排障用，不直接给用户）。
    # 三者都是 dict 列表（可序列化进 checkpoint），跨专家累积合并写入。
    degraded_tools: list[dict]
    blocked_tools: list[dict]
    tool_failures: list[dict]
    # 候选方案容器（任务书 §14，Phase 7 预留）：P0 单方案产出，无节点写入，
    # 仅占位——将来「出 A/B 两版让用户挑」时由规划层填充 CandidatePlan 快照。
    candidate_plans: list[dict]

    # === 输出 ===
    final_answer: str
    travel_context: dict
    # M2：结构化「为什么这样排」（reporter 产出 → PlanResponse.rationale）。
    # 必须入 schema——LangGraph 会剥离 schema 外的更新键（AGENTS.md 同款教训）。
    rationale: dict
    # Reporter 表达来源与用量归因；所有字段均为可序列化标量。
    reporter_meta: dict
    # M3-f 预算协商（budget expert 产出 → rationale.budget_negotiation）
    budget_negotiation: dict
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
        "live_search": state.get("live_search", {}),
        "task_results": state.get("task_results", []),
        "brief_missing": state.get("brief_missing", []),
        "intent": state.get("intent", ""),
        "query_destination": state.get("query_destination", ""),
        "slot_sources": state.get("slot_sources", {}),
        "clarification_options": state.get("clarification_options", []),
        "stage": state.get("stage", ""),
        "repair_rounds": state.get("repair_rounds", 0),
        "repair_log": state.get("repair_log", []),
        "partial_replan": state.get("partial_replan", {}),
        "partial_replan_result": state.get("partial_replan_result", {}),
        "preserved_days": state.get("preserved_days", []),
        "changed_days": state.get("changed_days", []),
        "scope_expansion_reason": state.get("scope_expansion_reason", ""),
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
        # 首末日用时（#82）：空串归一为 None，避免 ""/缺省抖动触发假重排
        "arrival_time": brief.arrival_time or None,
        "departure_time": brief.departure_time or None,
        "party_size": brief.party_size,
        "budget_cny": brief.budget_cny,
        "budget_constraint": brief.budget_constraint,
        "weather_conditions": brief.weather_conditions,
        "preferences": sorted(brief.preferences),
        "must_go": sorted(brief.must_go),
        "optional_go": sorted(brief.optional_go),
        "avoid": sorted(brief.avoid),
        "pace": brief.normalized_pace(),
        "tier": brief.tier,
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


def planning_reset(parent_plan_version: int | None = None) -> dict:
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
        "plan_parent_version": parent_plan_version,
        "plan_stability_baseline": {},
        "preserved_days": [],
        "changed_days": [],
        "scope_expansion_reason": "",
        "candidates": [],
        "live_search": {},
        "inspiration": {},
        "transit_query": {},
        "task_results": [],
        "day_plan": [],
        "must_go_unresolved": [],
        "itinerary": None,
        "validation": None,
        # 证据表随旧行程一起失效：evidences 描述的是「上一份计划引用了什么数据」，
        # 重规划后对新计划不再适用；不清会让历史污染键（如扁平 evidence 残留）
        # 跨轮存活，validator 下一次 check_source_trust 仍会炸（2026-10-01 实测）
        "evidences": {},
        "repair_rounds": 0,
        "repair_stalled": False,
        "repair_log": [],
        "last_repair_constraint_sig": "",
        "repair_no_improvement_streak": 0,
        "partial_replan": {},
        "partial_replan_done": False,
        "partial_replan_result": {},
        # Failure Policy 结局账随重规划清空：blocked_tools 残留会让 supervisor
        # 在新一轮直接终止（上一轮的「必须 X 点前到」不该阻断本轮新需求）
        "degraded_tools": [],
        "blocked_tools": [],
        "tool_failures": [],
        "expert_history": [],
        "last_expert_result": {},
        "supervisor_decision": {},
        "step_count": 0,
        "current_expert": "",
        "stage": "",
        "finished": False,
        "notes": [],
        "knowledge_refs": [],
        "candidate_plans": [],
        "plan_stability_baseline": {},
        "preserved_days": [],
        "changed_days": [],
        "scope_expansion_reason": "",
    }
