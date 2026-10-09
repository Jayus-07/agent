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

import re
from dataclasses import asdict

from backend.config import travel as T
from backend.shared.logger import logger
from backend.travel.agents.requirement_agent import (
    _RE_DATE_ISO,  # noqa: F401 — 兼容旧模块导出，契约测试锁定
    RequirementAgent,
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
    extract_lodging,
    extract_must_go,
    extract_origin,
    extract_pace,
    extract_party_size,
    extract_past_date,
    extract_preferences,
    extract_relative_date_expr,
    extract_start_date,
    extract_unsupported_city,
    extract_vague_time_expr,
    extract_weather_conditions,
    is_long_term_preference_message,
    party_size_source,
)
from backend.travel.core.intent import (
    NON_PLANNING_INTENTS,
    QUERY_INTENTS,
    TravelAdditionalTask,
    TravelChange,
    TravelIntent,
    TravelTaskParams,
    TravelTurnDecision,
    classify_intent,
)
from backend.travel.core.intent_signals import is_cancel_run_query, is_new_run_query
from backend.travel.graph_state import load_brief
from backend.travel.models.brief import TravelBrief
from backend.travel.node_span import traced_node
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


def _validated_brief_input(raw: object) -> dict:
    """读取 API 已校验的结构化 Brief；丢弃版本号等服务端所有字段。"""
    if not isinstance(raw, dict) or not raw:
        return {}
    try:
        parsed = TravelBrief.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 — 非法结构不覆盖规则提取结果
        logger.info("[TravelSlotFiller] brief_input 校验失败，忽略结构化输入: %s", exc)
        return {}
    fields = set(parsed.model_fields_set) - {"version"}
    return parsed.model_dump(mode="json", include=fields)


def _verified_ui_context(state: dict) -> dict:
    """只把确实属于当前行程的选中日期/POI 作为理解上下文。"""
    raw = state.get("ui_context") or {}
    itinerary = state.get("itinerary") or {}
    if hasattr(itinerary, "model_dump"):
        itinerary = itinerary.model_dump(mode="json")
    days = itinerary.get("days", []) if isinstance(itinerary, dict) else []
    if not isinstance(days, list):
        days = []

    result: dict[str, object] = {}
    selected_day = raw.get("selected_day") if isinstance(raw, dict) else None
    valid_days: set[int] = set()
    for offset, day in enumerate(days, 1):
        if isinstance(day, dict):
            try:
                valid_days.add(int(day.get("day_index") or offset))
            except (TypeError, ValueError):
                continue
    if isinstance(selected_day, int) and not isinstance(selected_day, bool):
        if selected_day in valid_days:
            result["selected_day"] = selected_day
    if selected_day is not None and "selected_day" not in result:
        # UI 显式传了无效日期时，不可退化成“在所有日期里找这个 POI”。
        return {}

    poi_id = raw.get("selected_poi_id") if isinstance(raw, dict) else None
    if isinstance(poi_id, str) and poi_id.strip():
        for offset, day in enumerate(days, 1):
            if not isinstance(day, dict):
                continue
            try:
                day_index = int(day.get("day_index") or offset)
            except (TypeError, ValueError):
                continue
            if result.get("selected_day") and day_index != result["selected_day"]:
                continue
            for item in day.get("items") or []:
                poi = item.get("poi") if isinstance(item, dict) else None
                if isinstance(poi, dict) and poi.get("poi_id") == poi_id:
                    result["selected_poi_id"] = poi_id
                    result["selected_poi_name"] = str(
                        poi.get("name") or item.get("title") or "")
                    break
            if result.get("selected_poi_id"):
                break
    return result


def _rule_additional_tasks(message: str, fresh: TravelBrief,
                           base: TravelBrief | None) -> list:
    """仅从原话和已存在 Brief 提取有限类型的独立查询任务。"""
    tasks = []
    origin = (fresh.origin or (base.origin if base else "")).strip()
    destination = (fresh.destination or (base.destination if base else "")).strip()
    travel_date = fresh.start_date or (base.start_date if base else None)
    date_text = travel_date.isoformat() if travel_date else ""
    if re.search(r"高铁|火车|动车|车票|车次|12306", message):
        tasks.append({
            "task_id": "train-1",
            "type": "query_train",
            "params": TravelTaskParams(
                origin=origin, destination=destination, travel_date=date_text,
            ).model_dump(mode="json"),
        })
    if re.search(r"天气|下雨|雨天|气温|温度|天气预报", message):
        tasks.append({
            "task_id": "weather-1",
            "type": "query_weather",
            "params": TravelTaskParams(
                city=destination, travel_date=date_text,
            ).model_dump(mode="json"),
        })
    return tasks


def _merge_turn_tasks(rule_tasks: list[dict], llm_decision,
                      message: str, fresh: TravelBrief,
                      brief: TravelBrief) -> list[dict]:
    """合并任务候选，并将 LLM 参数限制到原话/已验证 Brief 中的事实。"""
    tasks = [TravelAdditionalTask.model_validate(task) for task in rule_tasks]
    known = {
        "origin": fresh.origin or brief.origin,
        "destination": fresh.destination or brief.destination,
        "city": fresh.destination or brief.destination,
        "travel_date": (
            fresh.start_date.isoformat() if fresh.start_date else
            brief.start_date.isoformat() if brief.start_date else ""
        ),
    }
    seen_types = {task.type for task in tasks}
    if llm_decision is not None:
        for index, candidate in enumerate(llm_decision.additional_tasks, 1):
            if candidate.type in seen_types:
                continue
            values = candidate.params.model_dump(mode="python")
            for key in ("origin", "destination", "city", "travel_date"):
                proposed = str(values.get(key) or "").strip()
                trusted = str(known[key] or "").strip()
                if trusted:
                    values[key] = trusted
                elif proposed and proposed.casefold() in message.casefold():
                    values[key] = proposed
                else:
                    values[key] = ""
            if values.get("day_index") is not None:
                valid_day = values["day_index"] == (
                    fresh.days or brief.days)
                if not valid_day:
                    values["day_index"] = None
            tasks.append(TravelAdditionalTask(
                task_id=f"llm-{index}-{candidate.task_id}",
                type=candidate.type,
                params=TravelTaskParams.model_validate(values),
            ))
            seen_types.add(candidate.type)
    return [task.model_dump(mode="json") for task in tasks]


def _changes_from_partial(partial_request, message: str,
                          ui_context: dict) -> list[dict]:
    if partial_request is None:
        return []
    operation = partial_request.operation
    scope = {
        "day_index": partial_request.target_day or ui_context.get("selected_day"),
        "poi_id": ui_context.get("selected_poi_id"),
    }
    if operation == "pace":
        op, value = "set_pace", partial_request.pace
    elif operation == "replace_poi":
        return [TravelChange(
            op="replace_poi", scope=scope,
            target_name=(partial_request.remove_names or (None,))[0],
            replacement_name=(partial_request.add_names or (None,))[0],
            evidence_text=message,
        ).model_dump(mode="json")]
    elif operation == "remove_poi":
        return [TravelChange(
            op="remove_poi", scope=scope,
            target_name=(partial_request.remove_names or (None,))[0],
            evidence_text=message,
        ).model_dump(mode="json")]
    elif operation == "add_poi":
        return [TravelChange(
            op="add_poi", scope=scope,
            replacement_name=(partial_request.add_names or (None,))[0],
            evidence_text=message,
        ).model_dump(mode="json")]
    else:
        return []
    return [TravelChange(
        op=op, scope=scope, value=value, evidence_text=message,
    ).model_dump(mode="json")]


def _action_change(state: dict, ui_context: dict) -> dict | None:
    payload = state.get("action_payload") or {}
    if not isinstance(payload, dict):
        return None
    op = payload.get("operation") or payload.get("op")
    if op not in {
        "set_pace", "set_days", "set_budget", "set_destination",
        "set_preferences", "add_poi", "remove_poi", "replace_poi",
        "set_weather_condition",
    }:
        return None
    scope = {
        "day_index": payload.get("day_index") or ui_context.get("selected_day"),
        "poi_id": ui_context.get("selected_poi_id"),
        "time_slot": payload.get("time_slot"),
    }
    allowed = {"op": op, "scope": scope,
               "evidence_text": "结构化卡片操作"}
    for key in ("value", "target_name", "replacement_name"):
        if key in payload:
            allowed[key] = payload[key]
    try:
        return TravelChange.model_validate(allowed).model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001 — 非法卡片操作不得变成执行指令
        logger.info("[TravelSlotFiller] action_payload 被拒绝: %s", exc)
        return None


def _sanitize_llm_changes(changes: list, state: dict, message: str,
                          ui_context: dict) -> tuple[list[dict], list[str]]:
    """把模型提出的改单绑定到原话与当前行程中的可验证实体。"""
    itinerary = state.get("itinerary") or {}
    if hasattr(itinerary, "model_dump"):
        itinerary = itinerary.model_dump(mode="json")
    days = itinerary.get("days", []) if isinstance(itinerary, dict) else []
    if not isinstance(days, list):
        days = []

    entries: list[dict] = []
    valid_days: set[int] = set()
    for offset, day in enumerate(days, 1):
        if not isinstance(day, dict):
            continue
        try:
            day_index = int(day.get("day_index") or offset)
        except (TypeError, ValueError):
            continue
        valid_days.add(day_index)
        for item in day.get("items") or []:
            if not isinstance(item, dict):
                continue
            poi = item.get("poi") if isinstance(item.get("poi"), dict) else {}
            poi_id = str(poi.get("poi_id") or "").strip()
            name = str(poi.get("name") or item.get("title") or "").strip()
            if poi_id and name:
                entries.append({
                    "day_index": day_index, "poi_id": poi_id, "name": name,
                })

    from backend.travel.partial_replan import _target_day

    requested_day = _target_day(message)
    invalid_explicit_day = (
        requested_day is not None and requested_day not in valid_days)
    explicit_day = requested_day if not invalid_explicit_day else None
    selected_day = ui_context.get("selected_day")
    if selected_day not in valid_days:
        selected_day = None
    scope_day = (
        None if invalid_explicit_day else explicit_day or selected_day)
    selected_id = str(ui_context.get("selected_poi_id") or "")
    selected_entry = next(
        (entry for entry in entries if entry["poi_id"] == selected_id
         and (scope_day is None or entry["day_index"] == scope_day)),
        None,
    ) if not invalid_explicit_day else None
    selected_reference = bool(re.search(
        r"这个景点|那个景点|这地方|那个地方|选中的|刚才那个", message))

    safe_changes: list[dict] = []
    missing: list[str] = []
    grounded_values = {
        "set_pace": extract_pace(message),
        "set_days": extract_days(message),
        "set_budget": extract_budget(message),
        "set_destination": extract_destination(message),
        "set_preferences": extract_preferences(message),
    }
    for candidate in changes:
        raw = candidate.model_dump(mode="python")
        op = raw["op"]
        evidence = str(raw.get("evidence_text") or "").strip()
        if not evidence or evidence not in message:
            missing.append("desired_change")
            continue

        scope = raw.get("scope") or {}
        safe_day = scope_day
        target_entry = None
        if op in {"remove_poi", "replace_poi"}:
            if selected_reference:
                target_entry = selected_entry
            elif raw.get("target_name"):
                proposed_name = str(raw["target_name"]).strip()
                if proposed_name and proposed_name in message:
                    matches = [entry for entry in entries
                               if entry["name"].casefold()
                               == proposed_name.casefold()
                               and (safe_day is None
                                    or entry["day_index"] == safe_day)]
                    if len(matches) == 1:
                        target_entry = matches[0]
            if target_entry is None:
                if not invalid_explicit_day:
                    missing.append("selected_poi_id")
            else:
                safe_day = target_entry["day_index"]

        if op in {"add_poi", "remove_poi", "replace_poi"} and safe_day is None:
            if "selected_poi_id" not in missing:
                missing.append("target_day")

        raw["scope"] = {
            "day_index": safe_day,
            "poi_id": target_entry["poi_id"] if target_entry else None,
            "time_slot": scope.get("time_slot"),
        }
        if target_entry:
            raw["target_name"] = target_entry["name"]
        elif op in {"remove_poi", "replace_poi"}:
            raw["target_name"] = None

        if op in {"add_poi", "replace_poi"}:
            proposed_replacement = str(
                raw.get("replacement_name") or raw.get("value") or "").strip()
            if proposed_replacement and proposed_replacement in message:
                raw["replacement_name"] = proposed_replacement
            else:
                raw["replacement_name"] = None
                missing.append("replacement_poi")

        if op in grounded_values:
            trusted_value = grounded_values[op]
            if trusted_value in (None, "", []):
                missing.append("desired_change")
                continue
            raw["value"] = trusted_value
        elif op == "set_weather_condition":
            proposed = raw.get("value")
            values = proposed if isinstance(proposed, list) else [proposed]
            if not values or any(
                    not isinstance(value, str) or value not in message
                    for value in values):
                missing.append("desired_change")
                continue

        try:
            safe_changes.append(TravelChange.model_validate(raw).model_dump(
                mode="json"))
        except Exception as exc:  # noqa: BLE001 — 非法改单转追问，不落状态
            logger.info("[TravelSlotFiller] 拒绝未通过校验的模型改单: %s", exc)
            missing.append("desired_change")

    return safe_changes, list(dict.fromkeys(missing))


def _requires_turn_llm(message: str, intent, *, has_itinerary: bool,
                       partial_request, request_mode: str) -> bool:
    """只把规则盲区、复杂指代或未解析改单交给统一 LLM。"""
    if request_mode in {"plan", "action"} or partial_request is not None:
        return False
    if has_itinerary and re.search(
            r"这个景点|那个景点|这地方|那个地方|换个|换掉|刚才那个|"
            r"第二天.{0,12}(?:换|改|替换)|第[一二三四五六七八九十\d]+天.{0,12}(?:换|改|替换)",
            message):
        return True
    if intent is TravelIntent.MODIFY:
        return True
    return intent is None and len(message.strip()) >= 6


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


_READ_ONLY_REFERENCE_QUERY = re.compile(
    r"为什么|为何|怎么安排|安排原因|行程原因|预算.{0,8}(?:多少|怎么算|如何)|"
    r"(?:花费|费用|价格|多少钱|总价|总额)|这趟|这份行程|当前草案|"
    r"第[一二两三四五六七八九十\d]+天.{0,10}(?:安排|计划|理由|原因)",
)
_READ_ONLY_MUTATION = re.compile(
    r"换成|改成|改为|调整为|重排|重规划|重新规划|重新排行程|重新安排|"
    r"替换|删掉|删除|去掉|添加|增加|帮我规划|规划.{0,5}行程|生成.{0,5}行程|"
    r"(?:预算|天数).{0,8}(?:改到|改为|调整到|调整为)",
)


def _read_only_result(state: dict, *, blocked: bool, intent: str = "",
                      decision: TravelTurnDecision | None = None,
                      decision_meta: dict | None = None,
                      tasks: list[dict] | None = None,
                      inspiration: dict | None = None,
                      query_destination: str = "") -> dict:
    """只输出本轮问答元数据，不写 brief/itinerary/版本等规划状态。"""
    if blocked:
        intent = "read_only_blocked"
        decision = TravelTurnDecision(
            primary_action="answer", confidence=1.0, parse_source="rule")
        decision_meta = {
            "status": "blocked", "fallback_reason": "read_only_policy",
            "has_decision": False,
        }
        tasks = []
    elif decision is None:
        decision = TravelTurnDecision(
            primary_action=("discover" if intent == TravelIntent.DISCOVER.value
                            else "answer"),
            additional_tasks=[TravelAdditionalTask.model_validate(task)
                              for task in tasks or []],
            confidence=1.0,
            parse_source="rule",
        )
    elif tasks is not None:
        payload = decision.model_dump(mode="python")
        payload["additional_tasks"] = [
            TravelAdditionalTask.model_validate(task).model_dump(mode="python")
            for task in tasks
        ]
        decision = TravelTurnDecision.model_validate(payload)
    update = {
        "intent": intent,
        "turn_decision": decision.model_dump(mode="json"),
        "turn_decision_meta": decision_meta or {"status": "not_needed"},
        "brief_missing": [],
        "clarifications": [],
        "query_destination": query_destination,
        "inspiration": inspiration or {},
        "transit_query": {},
        "task_results": [],
        "partial_replan": {},
        "partial_replan_done": False,
        "partial_replan_result": {},
        "stage": "slot",
        "finished": False,
    }
    from backend.travel.core.events import emit_travel_event

    emit_travel_event(
        "requirement.interpreted",
        agent="requirement",
        brief=state.get("brief") or {},
        missing=[],
        assumptions=[],
        slot_sources={},
        intent=intent,
        clarification_options=[],
        confidence=("rule" if blocked else decision.parse_source),
        destination_change=False,
    )
    return update


def _read_only_slot_filler(state: dict) -> dict:
    """只读模式：仅允许问答/查询决策，任何规划动作都 fail-closed。"""
    message = str(state.get("user_message") or "").strip()
    itinerary = state.get("itinerary") or {}
    raw_brief = state.get("brief") or (
        itinerary.get("brief") if isinstance(itinerary, dict) else {}) or {}
    previous = load_brief({"brief": raw_brief}) if raw_brief else None
    has_itinerary = bool(itinerary)
    intent = classify_intent(
        message,
        has_itinerary=has_itinerary,
        has_destination=bool(extract_destination(message)),
    )
    reference_question = bool(_READ_ONLY_REFERENCE_QUERY.search(message))
    explicit_mutation = bool(_READ_ONLY_MUTATION.search(message))
    if explicit_mutation or intent in {TravelIntent.PLAN, TravelIntent.MODIFY}:
        if not (reference_question and not explicit_mutation):
            return _read_only_result(state, blocked=True)

    decision_outcome = None
    decision = None
    if reference_question or intent is None:
        from backend.travel.services.turn_decision_service import (
            interpret_turn_with_llm,
        )

        decision_outcome = interpret_turn_with_llm(
            message,
            context={
                "request_mode": "read_only",
                "has_itinerary": has_itinerary,
                "brief": previous.model_dump(mode="json") if previous else {},
                "plan_version": itinerary.get("plan_version")
                if isinstance(itinerary, dict) else None,
            },
        )
        decision = decision_outcome.decision
        if (decision is None
                or decision.primary_action not in {"answer", "discover"}
                or decision.changes
                or decision.confidence < 0.65):
            return _read_only_result(
                state, blocked=True, decision_meta=decision_outcome.meta())
        if decision.primary_action == "discover":
            intent = TravelIntent.DISCOVER
        else:
            intent = None

    if intent is not None and intent not in NON_PLANNING_INTENTS:
        return _read_only_result(state, blocked=True)

    fresh = TravelBrief()
    if intent in QUERY_INTENTS or intent is None:
        try:
            fresh = _requirement_agent.extract_fresh_brief(
                message, previous.destination if previous else "")
        except Exception:  # noqa: BLE001 — 只读抽取失败不应升级为规划
            logger.debug("[TravelSlotFiller] 只读问答槽位提取失败", exc_info=True)

    tasks = _rule_additional_tasks(message, fresh, previous)
    if decision is not None:
        tasks = _merge_turn_tasks(
            tasks, decision, message, fresh, previous or TravelBrief())

    query_destination = (
        fresh.destination or (previous.destination if previous else "")
    ).strip()
    inspiration: dict = {}
    if intent is TravelIntent.QUERY_STATIC and query_destination:
        try:
            from backend.travel.services.inspiration_service import (
                fetch_destination_inspiration,
            )

            inspiration = fetch_destination_inspiration(query_destination)
        except Exception:  # noqa: BLE001 — 外部只读检索软失败
            inspiration = {"status": "unavailable", "destination": query_destination}
    elif intent is TravelIntent.DISCOVER:
        from backend.travel.recommend import recommend_cities

        preferences = previous.preferences if previous else []
        inspiration = {"recommendations": [
            {"city": rec.city, "highlights": rec.highlights}
            for rec in recommend_cities(preferences, top=3)
        ]}

    result_intent = intent.value if intent is not None else "read_only_qa"
    return _read_only_result(
        state,
        blocked=False,
        intent=result_intent,
        decision=decision,
        decision_meta=(decision_outcome.meta() if decision_outcome else None),
        tasks=tasks,
        inspiration=inspiration,
        query_destination=query_destination,
    )


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
    if str(state.get("request_mode") or "") == "read_only":
        return _read_only_slot_filler(state)

    from backend.travel.graph_state import planning_reset

    message = state.get("user_message", "")
    long_term_preference = is_long_term_preference_message(message)
    # brief 基底：checkpoint 产物优先；无 checkpoint（STOP F3 reconstruct
    # 轮）时用适配器从 ConversationContext 重建的事实基底，二者皆无才从零抽
    raw_brief = state.get("brief") or state.get("reconstruct_brief") or {}
    previous = load_brief({"brief": raw_brief}) if raw_brief else None
    request_mode = str(state.get("request_mode") or "")
    structured_fields = _validated_brief_input(state.get("brief_input"))
    ui_context = _verified_ui_context(state)

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
    if request_mode == "plan":
        # 表单提交是明确的新规划入口，不把其字段误作现有行程的逐条改单。
        partial_request = None
    if (parsed_partial is not None
            and request_mode != "plan"
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
        if request_mode == "plan":
            intent = TravelIntent.PLAN
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
    # 交通查询与城市+天数同轮出现时按规划主任务处理，查票保留为附加任务。
    if (intent is TravelIntent.QUERY_TRANSIT and fresh.destination
            and fresh.days is not None):
        intent = TravelIntent.PLAN
    if (has_itinerary and previous is not None
            and re.search(r"(?:改成|改为|调整为)\s*\d{1,2}\s*[天日]|"
                          r"(?:预算.{0,6}(?:改成|改为|调整为)|"
                          r"(?:改成|改为|调整为).{0,6}预算)", message)
            and (fresh.days is not None or fresh.budget_cny is not None)):
        # 明确修改行程整体约束时，即使同句含「天气」等问句，也不能被
        # QUERY_DYNAMIC 抢成只读问答；实时查询留作独立附加任务。
        intent = TravelIntent.PLAN
    if request_mode == "plan":
        intent = TravelIntent.PLAN

    action_change = _action_change(state, ui_context)
    if request_mode == "action" and action_change is not None:
        intent = TravelIntent.MODIFY

    # 规则明确的表单/按钮/问答不调用模型；词表盲区或复杂指代走同一次
    # Structured Output，决策与槽位候选一并返回，不再串调两个旧服务。
    decision_outcome = None
    llm_decision = None
    decision_meta: dict = {"status": "not_needed", "fallback_reason": ""}
    slot_enrichment: dict = {}
    slot_parse_source = "rule"
    llm_party_guess = False
    if (_requires_turn_llm(
            message, intent, has_itinerary=has_itinerary,
            partial_request=partial_request, request_mode=request_mode)
            and not is_cancel_run_query(message)
            and not is_new_run_query(message)):
        from backend.travel.services.turn_decision_service import (
            interpret_turn_with_llm,
        )

        decision_outcome = interpret_turn_with_llm(
            message,
            context={
                "request_mode": request_mode,
                "has_itinerary": has_itinerary,
                "base_plan_version": state.get("base_plan_version"),
                "brief": previous.model_dump(mode="json") if previous else {},
                "ui_context": ui_context,
            },
        )
        decision_meta = decision_outcome.meta()
        llm_decision = decision_outcome.decision
        if llm_decision is not None and llm_decision.changes:
            safe_changes, safety_missing = _sanitize_llm_changes(
                llm_decision.changes, state, message, ui_context)
            if safety_missing or safe_changes != [
                    change.model_dump(mode="json")
                    for change in llm_decision.changes]:
                decision_payload = llm_decision.model_dump(mode="python")
                decision_payload["changes"] = safe_changes
                decision_payload["missing_fields"] = list(dict.fromkeys(
                    [*decision_payload["missing_fields"], *safety_missing]))
                decision_payload["needs_clarification"] = bool(
                    decision_payload["needs_clarification"] or safety_missing)
                llm_decision = TravelTurnDecision.model_validate(
                    decision_payload)
        if llm_decision is not None:
            if intent is None or (has_itinerary and partial_request is None):
                intent = {
                    "answer": TravelIntent.QUERY_STATIC,
                    "discover": TravelIntent.DISCOVER,
                    "create_plan": TravelIntent.PLAN,
                    "modify_plan": TravelIntent.MODIFY,
                    "replan_plan": TravelIntent.PLAN,
                }[llm_decision.primary_action]
            if llm_decision.brief_candidates:
                from backend.travel.services.llm_slot_enrichment_service import (
                    apply_candidates,
                    parse_candidates,
                )

                candidates, rejected = parse_candidates({
                    "candidates": [c.model_dump(mode="python")
                                   for c in llm_decision.brief_candidates],
                })
                protected = set(structured_fields)
                if previous is not None:
                    for slot in ("destination", "days", "preferences"):
                        if getattr(previous, slot):
                            protected.add(slot)
                    if previous.pace != "moderate":
                        protected.add("pace")
                    if previous.party_size != 1 or previous.adults is not None:
                        protected.add("party_size")
                usable = [
                    candidate for candidate in candidates
                    if candidate.slot not in protected
                    and (candidate.slot != "party_size"
                         or party_size_source(message) != "explicit")
                ]
                fresh, accepted = apply_candidates(fresh, usable)
                slot_parse_source = "rule+llm" if accepted else "rule_fallback"
                slot_enrichment = {
                    **decision_meta,
                    "used": bool(accepted),
                    "candidate_count": len(candidates),
                    "accepted_count": len(accepted),
                    "accepted_slots": accepted,
                    "rejected_count": rejected + len(candidates) - len(usable),
                }
                llm_party_guess = "party_size" in accepted
            else:
                slot_enrichment = {
                    **decision_meta,
                    "used": False,
                    "candidate_count": 0,
                    "accepted_count": 0,
                    "rejected_count": 0,
                }
        elif decision_outcome.status not in {"disabled", "empty"}:
            # 模型调用失败/结构非法时明确记录规则回退；此分支必须与
            # “有效决策但没有槽位候选”区分，便于追踪安全降级与质量指标。
            slot_parse_source = "rule_fallback"
            slot_enrichment = {
                **decision_meta,
                "used": False,
                "candidate_count": 0,
                "accepted_count": 0,
                "rejected_count": 0,
            }

    if partial_request is None:
        non_mutating = intent in NON_PLANNING_INTENTS
        brief = (
            previous.model_copy() if previous is not None else TravelBrief()
        ) if non_mutating else _requirement_service.merge(previous, fresh)
        if structured_fields and not non_mutating:
            # 表单字段是经 API/Pydantic 校验的显式事实，优先于文本正则与旧值。
            brief = brief.model_copy(update=structured_fields)
    else:
        non_mutating = False
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
    early_clarification_fields = (
        list(llm_decision.missing_fields)
        if llm_decision is not None and llm_decision.needs_clarification else [])
    if (has_itinerary and intent is TravelIntent.MODIFY
            and partial_request is None
            and (llm_decision is None or not llm_decision.changes)):
        refers_to_selected = bool(re.search(
            r"这个景点|那个景点|这地方|那个地方|选中的|刚才那个", message))
        if refers_to_selected and not ui_context.get("selected_poi_id"):
            early_clarification_fields.append("selected_poi_id")
        elif refers_to_selected:
            early_clarification_fields.append("replacement_poi")
        elif re.search(r"第[一二两三四五六七八九十\d]+天|第[一二两三四五六七八九十\d]+日", message):
            early_clarification_fields.append("desired_change")
        else:
            early_clarification_fields.append("target_day")
    if request_mode == "action":
        action_payload = state.get("action_payload") or {}
        action_op = ((action_payload.get("operation") or action_payload.get("op"))
                     if isinstance(action_payload, dict) else "")
        if action_change is None:
            early_clarification_fields.append(
                "replacement_poi" if action_op == "replace_poi" else "target_day")
        elif (action_change.get("op") == "replace_poi"
              and not action_change.get("replacement_name")
              and not action_change.get("value")):
            early_clarification_fields.append("replacement_poi")
        if (action_change and action_change.get("op") in {"remove_poi", "replace_poi"}
                and not (action_change.get("target_name")
                         or (action_change.get("scope") or {}).get("poi_id"))):
            early_clarification_fields.append("selected_poi_id")
    early_clarification_fields = list(dict.fromkeys(early_clarification_fields))
    # ── STOP 3/4（2026-10-08）：追问单一生成点 ────────────────────────
    # 规划轨（未分类/PLAN）走 ClarificationPlan → Renderer（LLM→模板降级，
    # 一次只问优先级最高的一个槽）；其余意图沿用模板出口（下游按意图转
    # 问答出口或清空，行为与收口前一致）。Reporter 只消费
    # state["clarifications"]，不再二次生成（STOP 5）。
    clarification_plan = None
    clarification_meta: dict = {"source": "template"}
    clarification = ""
    if missing or early_clarification_fields:
        if (intent in (None, TravelIntent.PLAN, TravelIntent.MODIFY)
                or early_clarification_fields):
            from backend.travel.services.clarification_renderer import (
                render_clarification,
            )

            clarification_plan = _clarification_service.build_clarification_plan(
                brief, message,
                unsupported_city=(
                    extract_unsupported_city(message) if message else ""),
                additional_missing=early_clarification_fields,
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

    # 统一决策是本轮语义事实源；旧 intent 仅作为现有 supervisor 的兼容投影。
    task_fresh = fresh.model_copy(update=structured_fields) if structured_fields else fresh
    rule_tasks = _rule_additional_tasks(message, task_fresh, previous)
    additional_tasks = _merge_turn_tasks(
        rule_tasks, llm_decision, message, task_fresh, brief)
    if request_mode == "plan":
        primary_action = "create_plan"
        changes = []
    elif request_mode == "action":
        primary_action = "modify_plan"
        changes = [action_change] if action_change else []
    elif llm_decision is not None:
        primary_action = llm_decision.primary_action
        changes = [change.model_dump(mode="json")
                   for change in llm_decision.changes]
    elif partial_request is not None:
        primary_action = "modify_plan"
        changes = _changes_from_partial(partial_request, message, ui_context)
    elif intent is TravelIntent.DISCOVER:
        primary_action = "discover"
        changes = []
    elif intent in NON_PLANNING_INTENTS:
        primary_action = "answer"
        changes = []
    elif previous is not None and brief_changed:
        primary_action = "replan_plan"
        changes = []
        field_ops = {
            "destination": "set_destination", "days": "set_days",
            "budget_cny": "set_budget", "pace": "set_pace",
            "preferences": "set_preferences",
        }
        for field in brief_changed_fields:
            op = field_ops.get(field)
            if op:
                changes.append(TravelChange(
                    op=op, value=getattr(brief, field),
                    evidence_text=message,
                ).model_dump(mode="json"))
    elif intent is TravelIntent.MODIFY:
        primary_action = "modify_plan"
        changes = []
    else:
        primary_action = "create_plan"
        changes = []

    clarification_fields: list[str] = list(early_clarification_fields)
    if llm_decision is not None and llm_decision.needs_clarification:
        clarification_fields.extend(llm_decision.missing_fields)
    if primary_action in {"create_plan", "replan_plan"} and missing:
        clarification_fields.extend(missing)
    if request_mode == "action" and action_change is None:
        op = str((state.get("action_payload") or {}).get("operation") or "")
        clarification_fields.append(
            "replacement_poi" if op == "replace_poi" else "target_day")
    for change in changes:
        if change.get("op") in {"add_poi", "remove_poi", "replace_poi"}:
            scope = change.get("scope") or {}
            if not scope.get("day_index") and not scope.get("poi_id"):
                clarification_fields.append("target_day")
            if (change.get("op") == "replace_poi"
                    and not change.get("replacement_name")
                    and not change.get("value")):
                clarification_fields.append("replacement_poi")
    clarification_fields = list(dict.fromkeys(clarification_fields))
    needs_clarification = bool(clarification_fields)
    if llm_decision is not None:
        confidence = llm_decision.confidence
        parse_source = "llm"
    elif request_mode == "plan" and structured_fields:
        confidence = 1.0
        parse_source = "structured"
    elif request_mode == "action" and action_change is not None:
        confidence = 1.0
        parse_source = "structured"
    else:
        confidence = 1.0 if intent is not None or partial_request else 0.75
        parse_source = (
            "rule_fallback" if decision_outcome is not None
            and decision_outcome.status not in {"disabled", "empty"}
            else "rule")
    turn_decision = TravelTurnDecision(
        primary_action=primary_action,
        changes=changes,
        additional_tasks=additional_tasks,
        brief_candidates=(
            [candidate.model_dump(mode="json")
             for candidate in llm_decision.brief_candidates]
            if llm_decision is not None else []),
        needs_clarification=needs_clarification,
        missing_fields=clarification_fields,
        confidence=confidence,
        parse_source=parse_source,
    )

    # 点击选项来自 ClarificationPlan（规则生成，LLM 零参与）：destination
    # 追问给城市 chips、days 追问给「快捷 3 天/自由输入」，全部可重发回域。
    clarification_options: list[dict] = []
    if clarification_plan is not None and intent in (None, TravelIntent.PLAN):
        clarification_options = _clarification_service.frontend_options(
            clarification_plan)
    if (intent in {TravelIntent.QUERY_STATIC, TravelIntent.QUERY_DYNAMIC,
                   TravelIntent.QUERY_TRANSIT,
                   TravelIntent.DISCOVER, TravelIntent.MODIFY}
            and not early_clarification_fields):
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

    # 实时 Tool IO 统一由 travel_auxiliary_tasks 执行；槽位层只产任务计划。
    transit_query: dict = {}

    update: dict = {
        "turn_decision": turn_decision.model_dump(mode="json"),
        "turn_decision_meta": decision_meta,
        "brief_missing": missing,
        "clarifications": [clarification] if clarification else [],
        "clarification_options": clarification_options,
        # LLM 理解层观测（Phase 2）：统一决策调用 meta 与
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
        # Checkpointer 会合并跨轮状态；附加任务结果严格限于当前轮。
        "task_results": [],
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
