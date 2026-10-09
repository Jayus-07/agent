"""旅游 Reporter 表达层：证据约束改写，失败时保留确定性模板。"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from backend.shared.logger import logger

_NUMBER_TOKEN = re.compile(r"(?<![A-Za-z])(?:[GDC]\s*\d{1,5}|\d+(?:\.\d+)?)(?![A-Za-z])", re.I)
_CHINESE_NUMBER_TOKEN = re.compile(
    r"[零〇一二两三四五六七八九十百千万]+"
    r"(?:年|月|日|号|天|人|元|块|公里|千米|米|分钟|小时|点|站|晚|间|趟|次|个|位|张|折|%)"
)
_WEEKDAY_TOKEN = re.compile(r"(?:周|星期)[一二三四五六日天]")
_PLACE_ACTION = re.compile(
    r"(?:推荐前往|推荐去|前往|抵达|到达|到访|安排在|改去|行程包含|"
    r"路线经过|途经|游览|参观|入住|住在|先去|再去|之后去|去|到)"
    r"([\u4e00-\u9fffA-Za-z0-9·]{2,12}?)"
    r"(?=再|然后|之后|并|等|的|景区|景点|酒店|车站|机场|查看|游玩|"
    r"游览|住宿|安排|出发|抵达|过夜|用餐|吃饭|[，。！？、；\s]|$)"
)
_PLACE_SUFFIX = re.compile(
    r"([\u4e00-\u9fff]{2,8}(?:市|县|区|镇|岛|山|湖|古镇|机场|车站|公园|景区|酒店|广场))"
)
_NON_PLACE_TARGETS = {"行程卡", "详情", "卡片", "页面", "本页", "下方"}
_FORBIDDEN_CLAIMS = (
    "已应用", "已经应用", "应用成功", "已保存", "保存成功", "已持久化",
    "正式版已更新", "行程已生效", "行程已经生效", "已生效",
    "已经生效", "正式行程已更新", "已预订", "预订成功",
)
_EVIDENCE_MARKERS = (
    "车次", "列车", "余票", "有票", "无票", "票价", "酒店", "房价",
    "价格", "已核验", "已验证", "营业", "开放", "预约", "景点", "景区",
    "路线", "行程顺序", "顺路", "绕路", "步行", "公交", "地铁", "打车",
    "换乘", "门票", "免费", "收费", "停业", "闭园", "排队", "堵车",
    "天气", "下雨", "晴天", "阴天", "降雨", "温度", "餐厅", "美食",
    "车站", "机场", "公里", "分钟", "小时",
)
_MAX_REPLY_CHARS = 240
_MAX_FACT_SNAPSHOT_CHARS = 12_000
_MAX_HUMAN_PAYLOAD_BYTES = 16_000
_MAX_FACT_TEXT_CHARS = 240
_PREVIEW_FIELDS_BY_CATEGORY = {
    "route": {"from", "to", "mode", "minutes", "km", "cost_cny"},
    "weather": {"date", "weather"},
    "budget": {"item", "cny"},
    "knowledge": {"text", "source"},
    "train": {
        "train_code", "train_no", "from_station", "to_station", "departure",
        "arrival", "departure_time", "arrival_time", "duration", "price",
        "seat_types", "second_class", "first_class", "business_class",
    },
    "train_price": {
        "train_code", "seat_types", "second_class", "first_class",
        "business_class", "soft_sleeper", "hard_sleeper",
    },
    "hotel": {"name", "title", "address", "price", "rating", "opening_hours"},
    "poi": {"name", "title", "address", "rating", "category", "opening_hours"},
    "restaurant": {"name", "title", "address", "price", "rating", "category"},
    "guide": {"title", "summary", "excerpt", "topic", "source"},
}
_CATEGORY_BY_TASK = {
    "query_train": "train",
    "query_weather": "weather",
    "query_poi": "poi",
    "query_hotel": "hotel",
}


def _render_prompt() -> tuple[str, str, str]:
    """取静态指令；动态用户/工具数据只通过独立 HumanMessage 传递。"""
    from backend.prompts.service import prompt_service

    rendered = prompt_service.render_sync(
        "travel.reporter",
        user_message="见独立 JSON 用户消息的 user_message 字段",
        verified_facts="见独立 JSON 用户消息的 verified_facts 字段",
        template_answer="见独立 JSON 用户消息的 template_answer 字段",
    )
    version = getattr(rendered, "version", None)
    return (
        rendered.text,
        f"travel.reporter@{version}" if version is not None else "",
        str(getattr(rendered, "source", "") or ""),
    )


def _bound_value(value: Any, text_limit: int, list_limit: int, depth: int = 0) -> Any:
    """递归限制动态事实的字段深度、集合宽度和字符串长度。"""
    if isinstance(value, str):
        if len(value) <= text_limit:
            return value
        if text_limit <= 0:
            return ""
        return value[:text_limit - 1] + "…"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if depth >= 5:
        return "[已截断]"
    if isinstance(value, list):
        return [
            _bound_value(item, text_limit, list_limit, depth + 1)
            for item in value[:list_limit]
        ]
    if isinstance(value, dict):
        return {
            str(key)[:80]: _bound_value(item, text_limit, list_limit, depth + 1)
            for key, item in list(value.items())[:16]
        }
    return _bound_value(str(value), text_limit, list_limit, depth + 1)


def _fit_fact_snapshot(facts: dict[str, Any]) -> dict[str, Any]:
    """缩减低优先级预览，保证传给模型的事实 JSON 有硬上限。"""
    for text_limit, list_limit in ((240, 8), (120, 4), (80, 2), (32, 2), (0, 1)):
        bounded = _bound_value(facts, text_limit, list_limit)
        encoded = json.dumps(bounded, ensure_ascii=False, default=str)
        if len(encoded.encode("utf-8")) <= _MAX_FACT_SNAPSHOT_CHARS:
            return bounded

    # 极端状态下保留路由、需求和任务状态，丢弃长文本与预览。
    return {
        "intent": str(facts.get("intent") or "")[:32],
        "primary_action": str(facts.get("primary_action") or "")[:32],
        "brief": _bound_value(facts.get("brief") or {}, 32, 1),
        "plan": _bound_value(facts.get("plan") or {}, 32, 1),
        "task_results": [
            {
                "task_id": str(item.get("task_id") or "")[:32],
                "type": str(item.get("type") or "")[:32],
                "status": str(item.get("status") or "")[:32],
                "data_status": str(item.get("data_status") or "")[:32],
            }
            for item in facts.get("task_results", [])[:8]
            if isinstance(item, dict)
        ],
    }


def _project_preview(preview: Any, category: str) -> list[dict[str, Any]]:
    """按任务类别挑选可用于答复的字段，跳过原始 Provider 附加字段。"""
    allowed = _PREVIEW_FIELDS_BY_CATEGORY.get(category)
    if not allowed or not isinstance(preview, list):
        return []
    projected = []
    for item in preview[:4]:
        if not isinstance(item, dict):
            continue
        projected.append({
            key: _bound_value(value, _MAX_FACT_TEXT_CHARS, 4)
            for key, value in item.items()
            if key in allowed and value not in (None, "", [])
        })
    return projected


def _clean_payload_text(value: Any, limit: int) -> str:
    text = str(value or "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
    return text[:limit]


def _build_human_payload(
    state: dict[str, Any], facts: dict[str, Any], template_answer: str,
) -> tuple[str, dict[str, Any]]:
    """序列化动态输入，并对整个用户消息体施加 UTF-8 字节硬上限。"""
    user_message = _clean_payload_text(state.get("user_message"), 500)
    template = _clean_payload_text(template_answer, 3_000)
    reduction_steps = (
        (500, 3_000, 240, 8),
        (400, 2_500, 120, 4),
        (300, 1_500, 80, 2),
        (200, 800, 32, 1),
        (100, 400, 0, 0),
    )
    for user_limit, template_limit, text_limit, list_limit in reduction_steps:
        bounded_facts = _bound_value(facts, text_limit, list_limit)
        payload = json.dumps({
            "user_message": user_message[:user_limit],
            "verified_facts": bounded_facts,
            "template_answer": template[:template_limit],
        }, ensure_ascii=False, default=str)
        if len(payload.encode("utf-8")) <= _MAX_HUMAN_PAYLOAD_BYTES:
            return payload, bounded_facts

    minimal_facts = {
        "intent": str(facts.get("intent") or "")[:32],
        "primary_action": str(facts.get("primary_action") or "")[:32],
        "task_results": [
            {
                "type": str(item.get("type") or "")[:32],
                "status": str(item.get("status") or "")[:32],
                "data_status": str(item.get("data_status") or "")[:32],
            }
            for item in facts.get("task_results", [])[:8]
            if isinstance(item, dict)
        ],
    }
    minimal_payload = json.dumps({
        "user_message": user_message[:100],
        "verified_facts": minimal_facts,
        "template_answer": template[:400],
    }, ensure_ascii=False)
    return minimal_payload, minimal_facts


def _fact_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    """只向表达模型提供受限的业务事实摘要，不传运行时对象或原始 Provider 包。"""
    decision = state.get("turn_decision") or {}
    brief = state.get("brief") or {}
    itinerary = state.get("itinerary") or {}
    if not isinstance(brief, dict):
        brief = {}
    if not isinstance(itinerary, dict):
        itinerary = {}

    changes = []
    for item in decision.get("changes") or []:
        if not isinstance(item, dict):
            continue
        changes.append({
            "op": item.get("op"),
            "scope": item.get("scope"),
            "value": item.get("value"),
            "evidence_text": item.get("evidence_text"),
        })

    tasks = []
    for item in (state.get("task_results") or [])[:8]:
        if not isinstance(item, dict):
            continue
        data = item.get("data") or {}
        preview = data.get("preview") if isinstance(data, dict) else []
        category = (
            str(data.get("category") or "") if isinstance(data, dict) else ""
        ) or _CATEGORY_BY_TASK.get(str(item.get("type") or ""), "")
        if (item.get("status") != "success"
                or item.get("data_status") not in {
                    "available", "warmed_or_local_estimate",
                }):
            preview = []
        tasks.append({
            "task_id": item.get("task_id"),
            "type": item.get("type"),
            "category": category,
            "status": item.get("status"),
            "data_status": item.get("data_status"),
            "result_count": item.get("result_count"),
            "preview": _project_preview(preview, category),
            "message": item.get("message"),
        })

    partial = state.get("partial_replan_result") or {}
    if not isinstance(partial, dict):
        partial = {}
    projection = state.get("_trace_plan_versions") or {}
    if not isinstance(projection, dict):
        projection = {}

    facts = {
        "intent": state.get("intent") or "",
        "primary_action": decision.get("primary_action") or "",
        "changes": changes[:8],
        "additional_tasks": [
            {"task_id": item.get("task_id"), "type": item.get("type")}
            for item in (decision.get("additional_tasks") or [])
            if isinstance(item, dict)
        ][:4],
        "brief": {
            key: brief.get(key)
            for key in ("destination", "days", "party_size", "budget_cny", "pace")
            if brief.get(key) not in (None, "", [])
        },
        "plan": {
            "plan_version": itinerary.get("plan_version"),
            "parent_version": itinerary.get("parent_plan_version")
            or state.get("plan_parent_version"),
            "status": itinerary.get("status"),
            "active_version": projection.get("active_plan_version"),
            "draft_version": projection.get("draft_plan_version"),
        },
        "partial_change": {
            "status": partial.get("status"),
            "changed_days": state.get("changed_days") or partial.get("changed_days") or [],
            "scope_expansion_reason": state.get("scope_expansion_reason") or "",
            "message": partial.get("message") or "",
        },
        "task_results": tasks,
        "degraded_tools": [
            {"tool": item.get("tool"), "note": item.get("note")}
            for item in (state.get("degraded_tools") or [])
            if isinstance(item, dict)
        ][:8],
        "blocked_tools": [
            {"tool": item.get("tool"), "user_message": item.get("user_message")}
            for item in (state.get("blocked_tools") or [])
            if isinstance(item, dict)
        ][:8],
        "persistence_status": state.get("persistence_status") or "",
    }
    if state.get("request_mode") == "read_only" and itinerary:
        days = itinerary.get("days") if isinstance(itinerary, dict) else []
        cost = itinerary.get("cost") if isinstance(itinerary, dict) else {}
        brief = itinerary.get("brief") if isinstance(itinerary, dict) else {}
        facts["read_only_plan"] = {
            "plan_version": itinerary.get("plan_version"),
            "brief": {
                key: brief.get(key)
                for key in ("destination", "days", "budget_cny", "pace", "preferences")
                if brief.get(key) not in (None, "", [])
            } if isinstance(brief, dict) else {},
            "cost_estimate_cny": {
                key: cost.get(key)
                for key in ("tickets", "meals", "lodging", "transit", "total")
                if cost.get(key) is not None
            } if isinstance(cost, dict) else {},
            "days": [
                {
                    "day_index": day.get("day_index"),
                    "items": [
                        {
                            "title": item.get("title"),
                            "start": item.get("start"),
                            "end": item.get("end"),
                            "note": item.get("note"),
                        }
                        for item in (day.get("items") or [])[:5]
                        if isinstance(item, dict)
                    ],
                }
                for day in (days or [])[:7]
                if isinstance(day, dict)
            ] if isinstance(days, list) else [],
        }
    return _fit_fact_snapshot(facts)


def _validate_reply(reply: str, evidence_text: str) -> tuple[str, str]:
    """阻止 Reporter 新造地点、日期、数字及旅游业务事实。"""
    text = (reply or "").strip()
    if not text:
        return "", "empty"
    if len(text) > _MAX_REPLY_CHARS:
        return "", "too_long"
    if "\n" in text or "\r" in text or "```" in text or "http://" in text or "https://" in text:
        return "", "forbidden_content"
    if any(marker in text for marker in _FORBIDDEN_CLAIMS):
        return "", "forbidden_claim"
    token_patterns = (_NUMBER_TOKEN, _CHINESE_NUMBER_TOKEN, _WEEKDAY_TOKEN)
    evidence_tokens = {
        match.group(0).casefold()
        for pattern in token_patterns
        for match in pattern.finditer(evidence_text)
    }
    for pattern in token_patterns:
        for match in pattern.finditer(text):
            if match.group(0).casefold() not in evidence_tokens:
                return "", "unsupported_numeric_claim"
    for marker in _EVIDENCE_MARKERS:
        if marker in text and marker not in evidence_text:
            return "", "unsupported_claim"

    place_candidates = {
        match.group(1).strip()
        for match in _PLACE_ACTION.finditer(text)
    }
    place_candidates.update(
        match.group(1).strip()
        for match in _PLACE_SUFFIX.finditer(text)
    )
    for place in place_candidates:
        if place in _NON_PLACE_TARGETS:
            continue
        if place not in evidence_text:
            return "", "unsupported_place"

    if re.search(r"先(?:去|到|前往).{0,16}(?:再|然后)(?:去|到|前往)", text):
        route_markers = ("路线", "行程顺序", "顺路", "途经", "先后顺序")
        if not any(marker in evidence_text for marker in route_markers):
            return "", "unsupported_claim"
    return text, ""


def _token_counts(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage_metadata", None) or {}
    metadata = getattr(response, "response_metadata", None) or {}
    if not isinstance(usage, dict):
        usage = {}
    if not usage and isinstance(metadata, dict):
        usage = metadata.get("token_usage") or {}
    if not isinstance(usage, dict):
        return 0, 0
    try:
        input_tokens = int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0)
        output_tokens = int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0)
    except (TypeError, ValueError):
        return 0, 0
    return input_tokens, output_tokens


def render_travel_reply(
    state: dict[str, Any],
    template_answer: str,
) -> tuple[str, dict[str, Any]]:
    """生成简短回复；失败、越权或缺少事实时返回确定性 Reporter 模板。"""
    from backend.config.travel import (
        TRAVEL_LLM_REPORTER_ENABLED,
        TRAVEL_LLM_REPORTER_TIMEOUT_MS,
    )

    meta: dict[str, Any] = {
        "source": "template",
        "model": "",
        "prompt_version": "",
        "prompt_source": "",
        "latency_ms": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "fallback_reason": "",
    }
    if not TRAVEL_LLM_REPORTER_ENABLED:
        meta["fallback_reason"] = "disabled"
        return template_answer, meta

    started = time.monotonic()
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        from backend.config import model_roles
        from backend.infra.async_utils import sync_call_with_timeout
        from backend.infra.llm.proxy import _build_llm_for, record_llm_result
        from backend.observability.llm_context import llm_attribution_scope
        from backend.shared.json_extractor import extract_json

        verified_facts = _fact_snapshot(state)
        human_payload, verified_facts = _build_human_payload(
            state, verified_facts, template_answer or "",
        )
        facts_json = json.dumps(verified_facts, ensure_ascii=False, default=str)
        try:
            system_prompt, prompt_version, prompt_source = _render_prompt()
        except Exception as exc:  # noqa: BLE001 — Prompt 故障直接回模板
            logger.warning("[TravelReporterLLM] Prompt 渲染失败，落模板: %s", exc)
            meta["fallback_reason"] = "prompt_unavailable"
            return template_answer, meta

        meta["prompt_version"] = prompt_version
        meta["prompt_source"] = prompt_source
        model_config = model_roles.resolve_effective("main") or {}
        model_name = str(model_config.get("value") or "")
        meta["model"] = model_name
        if not model_name:
            meta["fallback_reason"] = "no_model"
            return template_answer, meta

        llm = _build_llm_for(model_name).bind(temperature=0, max_tokens=220)
        with llm_attribution_scope(agent_domain="travel"):
            response = sync_call_with_timeout(
                llm.invoke,
                TRAVEL_LLM_REPORTER_TIMEOUT_MS / 1000.0,
                [SystemMessage(content=system_prompt),
                 HumanMessage(content=human_payload)],
            )
            try:
                record_llm_result(
                    response,
                    duration_ms=(time.monotonic() - started) * 1000,
                    model_name=model_name,
                )
            except Exception as exc:  # noqa: BLE001 — 计量失败不影响回复
                logger.warning("[TravelReporterLLM] Token 计量失败: %s", exc)

        input_tokens, output_tokens = _token_counts(response)
        meta["input_tokens"] = input_tokens
        meta["output_tokens"] = output_tokens
        raw = str(getattr(response, "content", "") or "")
        payload = extract_json(raw, source="travel.reporter")
        if not isinstance(payload, dict) or set(payload) != {"reply"}:
            meta["fallback_reason"] = "invalid_json"
        else:
            # Reporter 可总结模板及本轮已成功 Tool 的受限摘要；事实校验也使用同一
            # 快照，避免提示词允许使用结果卡数据、校验器却只认模板而全部回退。
            evidence = "\n".join((template_answer or "", facts_json))
            reply, reason = _validate_reply(str(payload.get("reply") or ""), evidence)
            if reply:
                meta["source"] = "llm"
                return reply, meta
            meta["fallback_reason"] = reason or "invalid_reply"
    except Exception as exc:  # noqa: BLE001 — 模型/超时/解析故障立即回模板
        logger.info("[TravelReporterLLM] 生成失败，落模板: %s: %s",
                    type(exc).__name__, str(exc)[:120])
        meta["fallback_reason"] = "llm_error"
    finally:
        meta["latency_ms"] = int((time.monotonic() - started) * 1000)
    return template_answer, meta


__all__ = ["render_travel_reply"]
