"""travel/slot_filler.py — 图节点编排 + 历史兼容入口（Phase 2 改造）

职责边界（Phase 2 冻结）：
  - LangGraph 节点 travel_slot_filler 的**编排体**（本文件唯一）：brief 基底
    选择、持久化状态、指纹变化处置（planning_reset 三分支）、notes 透明化；
  - **历史兼容入口**：re-export 迁移前的全部公开符号，旧调用（orchestration
    层 lazy import、存量测试）零改动；Phase 8 收口时删除；
  - is_avoid_patch_query 过渡真身（见下）。

需求理解本体（抽取/追问/TripBrief 生成）→ travel/agents/requirement_agent.py；
合并/指纹/版本/变更追踪 → travel/services/requirement_service.py；
重开/取消信号 → travel/core/intent_signals.py。

is_avoid_patch_query **过渡状态**：它内部调用 avoid 抽取（agents 层），不能
进 core（底座禁止反向依赖业务层），因此真身暂留本文件——Phase 5 生命周期
重构时随 avoid 通道重新评估归位，**当前不是最终架构归属**。
"""
from __future__ import annotations

from backend.config import travel as T
from backend.shared.logger import logger
from backend.travel.node_span import traced_node
from backend.travel.agents.requirement_agent import (
    RequirementAgent,
    _RE_DATE_ISO,
    build_clarification,
    extract_avoid,
    extract_budget,
    extract_date_range_days,
    extract_days,
    extract_days_range,
    extract_destination,
    extract_diet,
    extract_fresh_brief,
    extract_lodging,
    extract_must_go,
    extract_origin,
    extract_party_size,
    extract_pace,
    extract_preferences,
    extract_start_date,
    extract_unsupported_city,
    party_size_source,
)
from backend.travel.core.intent_signals import is_cancel_run_query, is_new_run_query
from backend.travel.graph_state import load_brief
from backend.travel.models.brief import TravelBrief
from backend.travel.services.requirement_service import (
    RequirementService,
    merge_brief,
)

# 无状态进程级单例：节点编排经此调用 Agent/Service 公开面。
_requirement_agent = RequirementAgent()
_requirement_service = RequirementService()

__all__ = [
    "RequirementAgent",
    "RequirementService",
    "build_clarification",
    "extract_avoid",
    "extract_brief",
    "extract_budget",
    "extract_date_range_days",
    "extract_days",
    "extract_days_range",
    "extract_destination",
    "extract_diet",
    "extract_lodging",
    "extract_must_go",
    "extract_origin",
    "extract_party_size",
    "extract_pace",
    "extract_preferences",
    "extract_start_date",
    "extract_unsupported_city",
    "extract_fresh_brief",
    "is_avoid_patch_query",
    "is_cancel_run_query",
    "is_new_run_query",
    "merge_brief",
    "party_size_source",
    "slot_filler_node",
]


def extract_brief(message: str, previous: TravelBrief | None = None) -> TravelBrief:
    """兼容组合器：新抽（RequirementAgent）→ 多轮合并（RequirementService）。

    语义与迁移前逐字一致：先抽 avoid 再抽 must_go 的顺序在 agent 内保持
    （前者参与后者的过滤）；有上一轮时合并。
    """
    fresh = extract_fresh_brief(message, previous.destination if previous else "")
    return merge_brief(previous, fresh) if previous else fresh


# avoid-PATCH 显式信号（STOP I2，STOP H Deferred #1）：completed 态
#（无 pending）的「不去鼓浪屿了」。此时 TravelPendingResolver 的补槽通道
# 不工作（补槽仅 pending 期），travel_prefilter 也不命中（该句 0 强信号词
# 0 城市名）——没有它，用户的避雷诉求就静默丢失。判定复用 extract_avoid
#（唯一抽取点）：捕获到具体地点才算（「不想去了」无地点不拦）。
def is_avoid_patch_query(message: str) -> bool:
    """消息是否为「避开某地点」的局部修改信号（纯函数，保守）。

    只回答「这句话在表达 avoid」，不回答路由该不该拦——后者由
    TravelPendingResolver 结合「活跃 travel 任务是否存在」决定。
    与 cancel/new_run 的优先级：整体取消与重开规划优先，避免
    「不去厦门了，重新规划杭州」被误判成 avoid。

    过渡归属说明见模块 docstring（Phase 5 重估，非最终架构位置）。
    """
    msg = (message or "").strip()
    if not msg or len(msg) > 40:
        return False
    if is_cancel_run_query(msg) or is_new_run_query(msg):
        return False
    return bool(extract_avoid(msg))


@traced_node(
    "travel_slot_filler", "旅游·槽位填充",
    metrics_fn=lambda u: {
        "missing_slots": len(u.get("brief_missing") or []),
        "brief_changed": int(bool(u.get("brief_change_reason"))),
        "clarify": int(bool(u.get("clarifications"))),
    },
)
def slot_filler_node(state: dict) -> dict:
    """槽位节点：抽取 → 合并 → 判定需求是否变化 → 计算缺失槽位与追问文案。

    编排职责（本文件）：状态读取、持久化判定、变化处置（planning_reset）、
    notes 透明化。抽取归 RequirementAgent，合并/指纹/版本归
    RequirementService——跨轮失效判定放这里而非 supervisor：节点是每轮
    **唯一**会改写 brief 的地方，判断依据（新旧指纹）只有它同时拿得到。
    """
    from backend.travel.graph_state import planning_reset

    message = state.get("user_message", "")
    # brief 基底：checkpoint 产物优先；无 checkpoint（STOP F3 reconstruct
    # 轮）时用适配器从 ConversationContext 重建的事实基底，二者皆无才从零抽
    raw_brief = state.get("brief") or state.get("reconstruct_brief") or {}
    previous = load_brief({"brief": raw_brief}) if raw_brief else None

    # 持久化状态（任务书 §10，Phase 4）：图入口每轮把当前状态写进 state
    # —— 这是该事实的唯一产生点，下游（supervisor_decision / reporter）
    # 只消费不重算。延迟 import：graph_builder 装配图时顶层 import 本模块，
    # 顶部 import 会成环（与 stamp_version 的延迟 import 同理）。
    from backend.travel.graph_builder import get_persistence_status

    persistence_status = get_persistence_status()
    # 强持久化策略（任务书 §10）：TRAVEL_REQUIRE_PERSISTENCE 开启且已降级时，
    # **拒绝复用跨轮产物** —— 多 worker 部署下 MemorySaver 各存一份，第二轮
    # 请求可能被路由到另一个 worker，跨轮改单会静默失效（用户拿到与上一轮
    # 无关的新行程还以为改成功了）。宁可每轮按全新规划处理，也要如实告知。
    require_fresh = (persistence_status == "degraded"
                     and T.TRAVEL_REQUIRE_PERSISTENCE)

    brief = _requirement_service.merge(
        previous,
        _requirement_agent.extract_fresh_brief(
            message, previous.destination if previous else ""),
    )

    # P1-1 偏好持久化（软失败，读写失败都不影响规划主链）：
    #   预填 —— 跨轮首轮（无上一轮 brief）且开启了偏好功能时，把历史偏好
    #   填进本轮没表达的槽位；发生在指纹计算之前，同轮内一次性完成。
    #   回写 —— 本轮用户明确表达的偏好（标签/节奏/忌口）upsert 落库。
    #   Memory 边界：长期偏好只存稳定字段（origin/preferences/pace/diet），
    #   一次性 TripBrief 字段（目的地/天数/预算/必去）绝不进长期 memory。
    if previous is None and T.TRAVEL_PREFS_ENABLED and state.get("user_id"):
        try:
            from backend.tools.travel import preferences as prefs_store

            saved = prefs_store.get_preferences(state.get("user_id", ""))
            if saved:
                if not brief.preferences and saved.get("preferences"):
                    brief.preferences = list(saved["preferences"])
                if not brief.origin and saved.get("origin"):
                    brief.origin = saved["origin"]
                if not brief.diet and saved.get("diet"):
                    brief.diet = saved["diet"]
                if (brief.pace == "moderate" and extract_pace(message) is None
                        and saved.get("pace")
                        in ("relaxed", "intense")):
                    brief.pace = saved["pace"]
                logger.info("[TravelSlotFiller] 已预填历史偏好: tags=%s pace=%s",
                            brief.preferences, brief.pace)
        except Exception:  # noqa: BLE001 — 预填失败按未填处理
            logger.debug("[TravelSlotFiller] 偏好预填失败", exc_info=True)
    if T.TRAVEL_PREFS_ENABLED and state.get("user_id"):
        try:
            from backend.tools.travel import preferences as prefs_store

            prefs_store.upsert_preferences(
                state.get("user_id", ""),
                origin=brief.origin or "",
                preferences=brief.preferences,
                pace=(brief.pace if brief.pace != "moderate" else ""),
                diet=brief.diet or "",
            )
        except Exception:  # noqa: BLE001 — 回写失败不影响本轮
            logger.debug("[TravelSlotFiller] 偏好回写失败", exc_info=True)

    missing = brief.missing_slots()
    clarification = _requirement_agent.build_clarification(brief, message)
    if clarification:  # M12：slot 追问计数（缺槽数分桶，软失败）
        try:
            from backend.observability.metrics import travel_slot_clarify_total
            n = len(brief.missing_slots())
            bucket = "0" if n <= 0 else ("1" if n == 1 else ("2-3" if n <= 3 else "4+"))
            travel_slot_clarify_total.labels(missing_count_bucket=bucket).inc()
        except Exception:
            pass

    # 指纹/版本/变更追踪（RequirementService，单一事实源在 graph_state）
    last_fingerprint = state.get("brief_fingerprint") or ""
    detection = _requirement_service.detect_brief_change(previous, brief, last_fingerprint)
    fingerprint = detection["fingerprint"]
    brief_changed = detection["changed"]
    # 仅在「有上一轮指纹且不同」时失效：首次进入（无指纹）不算变化

    # 版本链（任务书 §4）：指纹变化 = 需求实质变化 → version +1，并记录
    # 变化原因与差异字段（transit expert 盖版本章时消费）。必须先递增再
    # 构造 update —— update["brief"] 要带着新版本号落库。
    brief_change_reason = detection["change_reason"]
    brief_changed_fields: list[str] = detection["changed_fields"]
    if brief_changed and previous is not None:
        brief.version = detection["new_version"]

    logger.info(
        "[TravelSlotFiller] destination=%r days=%s missing=%s changed=%s",
        brief.destination, brief.days, missing, brief_changed,
    )

    # 透明化提示：猜测与区间说法不拦流程，但必须让用户看见、可纠正。
    # notes 每轮重写（旧轮提示对新规划已过时）；risk expert 在本轮末尾
    # 读取 state.notes 累加风险提示，不冲突。
    notes: list[str] = []
    # 日期区间透明化：「9月21到25日」按区间天数规划，让用户看得见换算结果
    date_range = extract_date_range_days(message)
    if date_range and brief.days == date_range[0]:
        notes.append(f"你说的「{date_range[1]}」共 {date_range[0]} 天，已按此规划；"
                     "想调整直接说「改成 N 天」")
    day_range = extract_days_range(message)
    if day_range and brief.days == day_range[1]:
        notes.append(
            f"你说的「{day_range[2]}」是区间说法，先按上限 {day_range[1]} 天规划；"
            "想调整直接说「改成 N 天」"
        )
    if party_size_source(message) == "guess" and brief.party_size > 1:
        notes.append(
            f"人数按 {brief.party_size} 人估算（根据你提到的同伴）；"
            "如不对，直接说「X个人」"
        )

    update: dict = {
        "brief": brief.model_dump(),
        "brief_missing": missing,
        "clarifications": [clarification] if clarification else [],
        "brief_fingerprint": fingerprint,
        "persistence_status": persistence_status,
        "stage": "slot",
    }

    previous_itinerary = state.get("itinerary") or {}
    previous_plan_version = int(previous_itinerary.get("plan_version") or 0) or None

    if require_fresh:
        # 强持久化策略下的降级处置（任务书 §10）：无条件清跨轮产物，
        # 即使指纹没变 —— 降级后端里留着的上一轮产物不可信（多 worker
        # 不共享、重启即失）。planning_reset 会清 notes，note 必须在其后写。
        update.update(planning_reset(previous_plan_version))
        notes.insert(
            0,
            "持久化已降级（当前为临时存储），本轮按全新规划处理；"
            "在恢复持久化之前，跨轮修改行程暂不可用",
        )
        logger.warning(
            "[TravelSlotFiller] REQUIRE_PERSISTENCE 开启且持久化降级，"
            "拒绝复用跨轮产物，按全新规划处理"
        )

    if brief_changed:
        # 需求变了：旧行程作废，连同执行态一起清掉重新规划。
        # 注意 planning_reset() 会把 notes 置空，所以 notes 必须在它之后写。
        update.update(planning_reset(previous_plan_version))
        # 变化原因与差异字段不进 planning_reset 清单：变化当轮产生、当轮被
        # transit expert 消费（盖版本章），跨轮保留也无害（下次变化会覆盖）。
        update["brief_change_reason"] = brief_change_reason
        update["brief_changed_fields"] = brief_changed_fields
        notes.insert(0, "需求已变化，已按新需求重新规划（上一版行程作废）")
        logger.info("[TravelSlotFiller] 需求指纹变化 %s→%s（brief v%d，变化字段 %s），清空规划产物重排",
                    last_fingerprint, fingerprint, brief.version,
                    brief_changed_fields or "未知")

    if (bool((state.get("travel_route") or {}).get("new_run"))
            or is_new_run_query(message)):
        # NEW_RUN（STOP F2）：用户显式「重新规划」。两个来源：路由层
        # resolver 在 pending 场景打的标记（travel_route.new_run），以及
        # 无 pending 时（任务已齐备出单）对消息本身的直接判定。与
        # brief_changed 分级：带新信息时上面指纹分支已重排（此处重复
        # reset 幂等）；不带新信息（「重新规划一下」指纹不变）时只有
        # 这里能保证出全新方案，而不是把上一版行程原样再输出一遍。
        update.update(planning_reset(previous_plan_version))
        notes.insert(0, "已按你的要求重新规划（新方案独立生成）")
        logger.info("[TravelSlotFiller] NEW_RUN 信号，规划产物已清空重排")

    update["notes"] = notes
    from backend.travel.core.events import emit_travel_event

    emit_travel_event(
        "requirement.interpreted",
        agent="requirement",
        brief=brief.model_dump(mode="json"),
        missing=missing,
        assumptions=notes,
        confidence="rule_based",
    )
    return update
