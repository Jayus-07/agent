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

from dataclasses import asdict

from backend.config import travel as T
from backend.shared.logger import logger
from backend.travel.node_span import traced_node
from backend.travel.agents.requirement_agent import (
    RequirementAgent,
    _RE_DATE_ISO,
    build_clarification,
    detect_multi_city,
    extract_avoid,
    extract_budget,
    extract_budget_constraint,
    extract_date_range_days,
    extract_days,
    extract_days_range,
    extract_destination,
    extract_diet,
    extract_fresh_brief,
    is_long_term_preference_message,
    extract_lodging,
    extract_must_go,
    extract_origin,
    extract_party_size,
    extract_past_date,
    extract_pace,
    extract_preferences,
    extract_relative_date_expr,
    extract_start_date,
    extract_unsupported_city,
    extract_vague_time_expr,
    extract_weather_conditions,
    party_size_source,
)
from backend.travel.core.intent import (
    NON_PLANNING_INTENTS,
    QUERY_INTENTS,
    TravelIntent,
    classify_intent,
)
from backend.travel.core.intent_signals import is_cancel_run_query, is_new_run_query
from backend.travel.graph_state import load_brief
from backend.travel.models.brief import TravelBrief
from backend.travel.partial_replan import parse_partial_request
from backend.travel.services.requirement_service import (
    RequirementService,
    merge_brief,
)

# 无状态进程级单例：节点编排经此调用 Agent/Service 公开面。
_requirement_agent = RequirementAgent()
_requirement_service = RequirementService()
# 追问计划唯一事实源（STOP 3）：Plan 构建/模板渲染/前端选项映射都在该模块
from backend.travel.services import clarification_service as _clarification_service

__all__ = [
    "RequirementAgent",
    "RequirementService",
    "build_clarification",
    "extract_avoid",
    "extract_brief",
    "extract_budget",
    "extract_budget_constraint",
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
    "extract_weather_conditions",
    "is_long_term_preference_message",
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
    long_term_preference = is_long_term_preference_message(message)
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

    has_itinerary = bool(state.get("itinerary"))
    parsed_partial = parse_partial_request(message) if has_itinerary else None
    # 没有点名日期的「改轻松一点」是全局节奏槽位变化，应触发完整重排；
    # 只有「第一天别太赶」这类明确目标日才走局部 pace 修改。
    partial_request = parsed_partial
    if (parsed_partial is not None
            and parsed_partial.operation == "pace"
            and parsed_partial.target_day is None):
        partial_request = None
    # 先过意图门，再提取会影响 TripBrief 的候选字段。查询类为了回答问题
    # 仍可抽取 query_destination，但绝不把它 merge 进本次 TripBrief。
    if partial_request is not None:
        # 局部改单以既有 brief 为基底；本轮「不要去/换成」不能被合并成
        # 全局 avoid/must_go 后触发整份重排。
        intent = TravelIntent.MODIFY
        fresh = TravelBrief()
        non_mutating = False
        brief = previous.model_copy() if previous is not None else TravelBrief()
    else:
        intent = classify_intent(
            message,
            has_itinerary=has_itinerary,
            has_destination=False,
        )
        fresh = TravelBrief()
        if intent not in {
            TravelIntent.SOCIAL,
            TravelIntent.META,
            TravelIntent.OUT_OF_SCOPE,
        } or long_term_preference:
            fresh = _requirement_agent.extract_fresh_brief(
                message, previous.destination if previous else "")
            if intent is None:
                intent = classify_intent(
                    message,
                    has_itinerary=has_itinerary,
                    has_destination=bool(
                        fresh.destination or (previous and previous.destination)),
                )
    if intent is None and T.TRAVEL_LLM_INTENT_ENABLED:
        # D 批理解层（#97/#98/#103）：只在词表盲区补判（铁律：词表能接住的
        # 永不过 LLM）。LLM 只产 intent 家族，字段抽取仍归词表正则——
        # 结构上不可能改行程；失败回落 None 走既有链路。
        from backend.travel.services.llm_intent_service import classify_intent_llm

        llm_intent = classify_intent_llm(
            message,
            has_itinerary=has_itinerary,
            has_destination=bool(fresh.destination or (previous and previous.destination)),
        )
        if llm_intent:
            # TravelIntent 用顶部 import——函数内重复 import 会把它标记成
            # 局部名，下游 `intent is TravelIntent.MODIFY` 即 UnboundLocalError
            # （实机踩过：首轮规划直接 failed）
            intent = TravelIntent(llm_intent)
            logger.info("[TravelSlotFiller] 词表盲区由 LLM 补判 intent=%s", intent.value)
    # ── STOP 1（2026-10-08）：LLM Slot Semantic Fallback ─────────────
    # 触发门禁（全部满足才调，单轮 ≤1 次）：规划轨（未分类或 PLAN）+
    # 合并后仍缺 required 槽 + 非 cancel/new_run。候选只填空槽
    # （apply_candidates 确定性合并），规则值永不被覆盖；任何失败回落
    # 纯规则结果，slot_parse_source 如实记录三种来源供 trace 消费。
    # 注意必须在 merge 之前改 fresh——merge 消费的就是这里的产物。
    slot_enrichment: dict = {}
    slot_parse_source = "rule"
    llm_party_guess = False
    if (partial_request is None
            and intent in (None, TravelIntent.PLAN)
            and not is_cancel_run_query(message)
            and not is_new_run_query(message)):
        pending_missing = (
            _requirement_service.merge(previous, fresh).missing_slots()
            if previous is not None else fresh.missing_slots()
        )
        if pending_missing:
            from backend.travel.services.llm_slot_enrichment_service import (
                apply_candidates,
                enrich_slots,
            )

            outcome = enrich_slots(message, missing_slots=pending_missing)
            # 显式人数表达（「3个人」「2个大人」）时丢弃 LLM party 候选：
            # apply_candidates 是纯函数看不到原话，显式 1 与缺省 1 不可区分，
            # 这道过滤放在持有原话的编排层。
            usable_candidates = [
                c for c in outcome.candidates
                if c.slot != "party_size"
                or party_size_source(message) != "explicit"]
            fresh, accepted_slots = apply_candidates(
                fresh, usable_candidates)
            outcome.accepted = [
                c for c in usable_candidates if c.slot in accepted_slots]
            slot_parse_source = "rule+llm" if outcome.used else "rule_fallback"
            slot_enrichment = outcome.meta()
            if accepted_slots:
                logger.info(
                    "[TravelSlotFiller] LLM 富化接受槽位 %s（status=%s）",
                    accepted_slots, outcome.status)
                if "party_size" in accepted_slots:
                    # 富化人数是 guess 级事实（规则没接住、LLM 有据补全），
                    # 口径与同伴推断对齐——不让透明化提示误报「按 1 人默认」
                    llm_party_guess = True

    if partial_request is None:
        non_mutating = intent in NON_PLANNING_INTENTS
        brief = (
            previous.model_copy() if previous is not None else TravelBrief()
        ) if non_mutating else _requirement_service.merge(previous, fresh)
    query_destination = ""
    if intent in QUERY_INTENTS:
        query_destination = fresh.destination or (
            previous.destination if previous else "")
    # M3-e 方案档位：fresh.tier 缺省恒为 economy，无法区分「说了经济型」
    # 与「没提」；这里用原话显式判定并在 merge 后覆盖（含降档回 economy）。
    from backend.travel.agents.requirement_agent import extract_tier
    explicit_tier = extract_tier(message) if not non_mutating else ""
    if explicit_tier:
        brief.tier = explicit_tier

    # 天数上限（验收 #72）：超长需求（30/60 天）按上限裁剪，明示不静默。
    # 放 merge 之后统一判——上一轮遗留的超限天数同样被兜住。
    days_clamped = False
    if not non_mutating and brief.days and brief.days > T.TRAVEL_MAX_DAYS:
        logger.info("[TravelSlotFiller] 天数 %s 超上限，裁剪为 %s",
                    brief.days, T.TRAVEL_MAX_DAYS)
        brief.days = T.TRAVEL_MAX_DAYS
        days_clamped = True

    # P1-1 偏好持久化（软失败，读写失败都不影响规划主链）：
    #   预填 —— 跨轮首轮（无上一轮 brief）且开启了偏好功能时，把历史偏好
    #   填进本轮没表达的槽位；发生在指纹计算之前，同轮内一次性完成。
    #   回写 —— 本轮用户明确表达的偏好（标签/节奏/忌口）upsert 落库。
    #   Memory 边界：长期偏好只存稳定字段（origin/preferences/pace/diet），
    #   一次性 TripBrief 字段（目的地/天数/预算/必去）绝不进长期 memory。
    if (not non_mutating and previous is None and T.TRAVEL_PREFS_ENABLED
            and state.get("user_id")):
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
    if (long_term_preference and T.TRAVEL_PREFS_ENABLED
            and state.get("user_id")):
        try:
            from backend.tools.travel import preferences as prefs_store

            prefs_store.upsert_preferences(
                state.get("user_id", ""),
                preferences=extract_preferences(message),
                pace=extract_pace(message) or "",
                diet=extract_diet(message),
            )
        except Exception:  # noqa: BLE001 — 回写失败不影响本轮
            logger.debug("[TravelSlotFiller] 偏好回写失败", exc_info=True)

    # 会话意图（v3 §2.1，P0-A 裁决 #1）：先判意图，后查条件。没有意图层
    # 时「丽江好玩吗」会因缺天数被误追问「玩几天」——supervisor 拿到
    # intent 后转问答出口，不再按规划链走。
    missing = brief.missing_slots()
    # ── STOP 3/4（2026-10-08）：追问单一生成点 ────────────────────────
    # 规划轨（未分类/PLAN）走 ClarificationPlan → Renderer（LLM→模板降级，
    # 一次只问优先级最高的一个槽）；其余意图沿用模板出口（下游按意图转
    # 问答出口或清空，行为与收口前一致）。Reporter 只消费
    # state["clarifications"]，不再二次生成（STOP 5）。
    clarification_plan = None
    clarification_meta: dict = {"source": "template"}
    clarification = ""
    if missing:
        if intent in (None, TravelIntent.PLAN):
            from backend.travel.services.clarification_renderer import (
                render_clarification,
            )

            clarification_plan = _clarification_service.build_clarification_plan(
                brief, message,
                unsupported_city=(
                    extract_unsupported_city(message) if message else ""),
            )
            if clarification_plan is not None:
                clarification, clarification_meta = render_clarification(
                    clarification_plan, message)
        else:
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

    # MODIFY 意图退让（v3 §2.1）：抽取器已把改动理解成结构化字段（avoid/
    # must_go/天数…指纹变化）时走既有「重排」链——那是有校验兜底的路径，
    # 只有「第二天换成室内」这类抽取器理解不了的逐条改单才转问答出口。
    if intent is TravelIntent.MODIFY and (brief_changed or missing):
        intent = None

    # 点击选项来自 ClarificationPlan（规则生成，LLM 零参与）：destination
    # 追问给城市 chips、days 追问给「快捷 3 天/自由输入」，全部可重发回域。
    clarification_options: list[dict] = []
    if clarification_plan is not None and intent in (None, TravelIntent.PLAN):
        clarification_options = _clarification_service.frontend_options(
            clarification_plan)
    if intent in {TravelIntent.QUERY_STATIC, TravelIntent.QUERY_DYNAMIC,
                  TravelIntent.QUERY_TRANSIT,
                  TravelIntent.DISCOVER, TravelIntent.MODIFY}:
        clarification = ""

    logger.info(
        "[TravelSlotFiller] destination=%r days=%s missing=%s changed=%s",
        brief.destination, brief.days, missing, brief_changed,
    )

    # 透明化提示：猜测与区间说法不拦流程，但必须让用户看见、可纠正。
    # notes 每轮重写（旧轮提示对新规划已过时）；risk expert 在本轮末尾
    # 读取 state.notes 累加风险提示，不冲突。
    notes: list[str] = []
    party_source = party_size_source(message)
    slot_sources = {
        "destination": (
            "explicit" if fresh.destination else
            ("inferred" if previous and previous.destination else "missing")
        ),
        "days": (
            "explicit" if fresh.days is not None else
            ("inferred" if previous and previous.days is not None else "missing")
        ),
        "party_size": (
            "explicit" if party_source == "explicit" else
            "guess" if party_source == "guess" else
            "inferred" if previous is not None else "default"
        ),
    }
    if long_term_preference:
        slot_sources["long_term_preference"] = "explicit"
    if llm_party_guess:
        slot_sources["party_size"] = "guess"
    if brief.budget_cny is not None:
        slot_sources["budget_constraint"] = brief.budget_constraint
    if brief.weather_conditions:
        notes.append(
            "已记录天气条件："
            + "、".join(
                f"第{item.get('day_index')}天下雨时优先安排室内"
                if item.get("day_index") else "下雨时优先安排室内"
                for item in brief.weather_conditions
            )
        )
    if (not non_mutating and slot_sources["party_size"] == "default"
            and brief.party_size == 1):
        notes.append("未说明同行人数，本次按 1 人默认；如不对，直接告诉我人数")
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
    # 人群节奏派生回显（验收 #80）：按同行人群默认的档位让用户看得见可纠正
    if (extract_pace(message) is None
            and party_size_source(message) in ("guess", "explicit")):
        from backend.travel.agents.requirement_agent import extract_group_pace

        group_pace = extract_group_pace(message)
        if group_pace and brief.pace == group_pace:
            pace_label = {"relaxed": "轻松", "intense": "紧凑"}.get(
                group_pace, group_pace)
            notes.append(
                f"按你的同行人群默认「{pace_label}」节奏排程"
                "（每天地点数/时长相应收紧或放宽）；想调整直接说节奏")

    # 相对日期回显（验收 #63）：「下周五」这类说法已换算成具体日期，
    # 让用户看得见换算结果、可纠正（「周末」歧义取周六也在此回显）。
    rel_expr = extract_relative_date_expr(message)
    if rel_expr and brief.start_date:
        weekday_cn = "一二三四五六日"[brief.start_date.weekday()]
        notes.append(
            f"出发日期按你说的「{rel_expr}」解析为 "
            f"{brief.start_date.strftime('%m月%d日')}（周{weekday_cn}）；"
            "想改直接说具体日期（如「10月20日出发」）"
        )
    # 过去日期拦截（验收 #71）：完整日期早于今天时不采用，明示原因
    past_date = extract_past_date(message)
    if past_date is not None and not brief.start_date:
        notes.append(
            f"你说的出发日期「{past_date.isoformat()}」已过去，本次未采用；"
            "请说一个未来的日期（如「10月20日出发」）"
        )
    # 模糊时间词（验收 #64）：「月底」无法唯一定日，不猜，明示当前口径
    vague_time = extract_vague_time_expr(message)
    if vague_time and not brief.start_date:
        notes.append(
            f"你说的「{vague_time}」我无法确定具体日期，先按「第 1 天」排；"
            "确定后告诉我具体日期（如「10月20日出发」），我会重排"
        )
    # 首末日时间回显（验收 #82）：到达/离开时刻已纳入排程窗口
    if brief.arrival_time:
        notes.append(
            f"已按你 {brief.arrival_time} 到达安排首日（从到达后开始排）；"
            "如不对，直接说「改为 X 点到」"
        )
    if brief.departure_time:
        notes.append(
            f"已按你 {brief.departure_time} 离开安排末日（此前收尾）；"
            "如不对，直接说「改为 X 点走」"
        )
    # 多城市检测（验收 #73）：仍按主目的地（最左）出单，但必须明示
    # 其余城市未纳入，而不是错误地按单城市默默处理。
    extra_cities = detect_multi_city(message)
    if intent is TravelIntent.PLAN and extra_cities and brief.destination:
        notes.append(
            f"当前支持单城市规划：已按「{brief.destination}」规划，你提到的"
            f"「{'、'.join(extra_cities)}」暂未纳入本次行程；"
            "可分开逐城规划"
        )
    # 天数上限回显（验收 #72）
    if days_clamped:
        notes.append(
            f"行程天数最多支持 {T.TRAVEL_MAX_DAYS} 天，已按 {T.TRAVEL_MAX_DAYS} 天规划；"
            "如需更长行程请分段规划"
        )

    # QUERY_STATIC：一轮一次定向灵感检索（v3 §3.1），产出三态灵感包供
    # reporter 渲染；检索失败不阻塞（status=unavailable 如实呈现）。
    inspiration: dict = {}
    if intent is TravelIntent.QUERY_STATIC and query_destination:
        from backend.travel.services.inspiration_service import (
            fetch_destination_inspiration,
        )

        inspiration = fetch_destination_inspiration(query_destination)
    elif intent is TravelIntent.DISCOVER:
        from backend.travel.recommend import recommend_cities

        inspiration = {"recommendations": [
            {"city": rec.city, "highlights": rec.highlights}
            for rec in recommend_cities(brief.preferences or [], top=3)
        ]}

    # QUERY_TRANSIT：车票预取（2026-10-08 #2）。仿 inspiration 模式：失败
    # 不阻塞（status=failed 如实呈现）。缺出发地/目的地不猜——reporter 带
    # 示例追问；缺日期按明天兜底并明示（12306 只收今天与未来日期）。
    transit_query: dict = {}
    if intent is TravelIntent.QUERY_TRANSIT:
        transit_destination = query_destination.strip()
        transit_origin = (
            fresh.origin or (previous.origin if previous else "")).strip()
        if not transit_destination:
            transit_query = {"status": "missing_destination"}
        elif not transit_origin:
            transit_query = {
                "status": "missing_origin",
                "destination": transit_destination,
            }
        else:
            transit_date = (fresh.start_date.isoformat()
                            if fresh.start_date else "")
            date_defaulted = False
            if not transit_date:
                from datetime import date as _date, timedelta as _timedelta

                transit_date = (_date.today() + _timedelta(days=1)).isoformat()
                date_defaulted = True
            try:
                from backend.travel.services import live_search_service

                payload = live_search_service.search_trains(
                    from_station=transit_origin,
                    to_station=transit_destination,
                    travel_date=transit_date, limit=6)
                transit_query = {
                    "status": "ok",
                    "origin": transit_origin,
                    "destination": transit_destination,
                    "date": transit_date,
                    **(payload if isinstance(payload, dict) else {}),
                }
            except Exception as exc:  # noqa: BLE001 — 查询失败如实呈现不阻塞
                logger.warning("[TravelSlotFiller] 车票预取失败: %s", exc)
                transit_query = {
                    "status": "failed",
                    "origin": transit_origin,
                    "destination": transit_destination,
                    "date": transit_date,
                }
            if date_defaulted:
                notes.append(
                    f"未说出发日期，车票按明天（{transit_date}）查；"
                    "要查其他日期直接说日期（如「10月20日的车」）"
                )

    update: dict = {
        "brief_missing": missing,
        "clarifications": [clarification] if clarification else [],
        "clarification_options": clarification_options,
        # LLM 理解层观测（STOP 1/3/4）：解析来源、两次 LLM 调用 meta 与
        # 追问计划。全部可序列化标量/dict；LLM 在结构上不写业务状态，
        # 这些键只是观测投影。必须入 schema（graph_state.TypedDict）。
        "slot_parse_source": slot_parse_source,
        "slot_llm_meta": slot_enrichment,
        "clarification_plan": (
            clarification_plan.model_dump()
            if clarification_plan is not None else {}),
        "clarification_source": (
            clarification_meta.get("source", "") if clarification else ""),
        "clarification_meta": clarification_meta if clarification else {},
        "query_destination": query_destination,
        "slot_sources": slot_sources,
        "destination_change": "destination" in brief_changed_fields,
        "persistence_status": persistence_status,
        "stage": "slot",
        "finished": False,
        "intent": intent.value if intent else "",
        "inspiration": inspiration,
        "partial_replan": asdict(partial_request) if partial_request else {},
        "partial_replan_done": False,
        "partial_replan_result": {},
    }
    # 无既有 Trip 的轻量消息不能把空 brief/fingerprint 写进 checkpoint；
    # 否则下一条真正 PLAN 会被误判成「从空需求变化」，凭空产生一次 reset。
    if previous is not None or not non_mutating:
        update["brief"] = brief.model_dump()
        update["brief_fingerprint"] = fingerprint

    previous_itinerary = state.get("itinerary") or {}
    previous_plan_version = int(previous_itinerary.get("plan_version") or 0) or None

    if require_fresh and not non_mutating:
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
        previous_itinerary = state.get("itinerary")
        update.update(planning_reset(previous_plan_version))
        if previous_itinerary:
            update["plan_stability_baseline"] = {
                "itinerary": previous_itinerary,
                "changed_fields": list(brief_changed_fields),
            }
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

    # reset 清理的是旧产物；本轮问答检索结果必须在 reset 之后写回。
    update["inspiration"] = inspiration
    update["transit_query"] = transit_query
    update["notes"] = notes

    # M3-g 城市指南预热：城市级知乎/RAG 检索 fire-and-forget（结果写
    # 7 天缓存供速览卡/抽屉秒出）。不阻塞规划主链、失败静默。
    _dest = (brief.destination or "").strip()
    if _dest and not non_mutating:
        try:
            from concurrent.futures import ThreadPoolExecutor

            from backend.travel.services import city_guide_service

            ThreadPoolExecutor(max_workers=1).submit(
                city_guide_service.prime_city_guide, _dest)
        except Exception:  # noqa: BLE001 — 预热失败静默
            pass
    from backend.travel.core.events import emit_travel_event

    emit_travel_event(
        "requirement.interpreted",
        agent="requirement",
        brief=brief.model_dump(mode="json"),
        missing=missing,
        assumptions=notes,
        slot_sources=slot_sources,
        intent=intent.value if intent else "",
        clarification_options=clarification_options,
        # 解析来源三态（rule / rule+llm / rule_fallback），取代硬编码
        confidence=slot_parse_source,
        destination_change="destination" in brief_changed_fields,
    )
    return update
