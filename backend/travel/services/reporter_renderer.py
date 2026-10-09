"""旅游 Reporter 表达层：证据约束改写，失败时保留确定性模板。"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from backend.shared.logger import logger

_NUMBER_TOKEN = re.compile(r"(?<![A-Za-z])(?:[GDC]\s*\d{1,5}|\d+(?:\.\d+)?)(?![A-Za-z])", re.I)
_FORBIDDEN_CLAIMS = (
    "已应用", "已经应用", "应用成功", "已保存", "保存成功", "已持久化",
    "正式版已更新", "行程已生效", "行程已经生效", "已生效",
    "已经生效", "正式行程已更新", "已预订", "预订成功",
)
_EVIDENCE_MARKERS = (
    "车次", "列车", "余票", "有票", "无票", "票价", "酒店", "房价",
    "价格", "已核验", "已验证", "营业", "开放",
)
_MAX_REPLY_CHARS = 240


def _render_prompt(
    user_message: str,
    verified_facts: str,
    template_answer: str,
) -> tuple[str, str, str]:
    """从 Prompt Registry 取版本化模板；默认 YAML 由 PromptService 降级。"""
    from backend.prompts.service import prompt_service

    rendered = prompt_service.render_sync(
        "travel.reporter",
        user_message=user_message,
        verified_facts=verified_facts,
        template_answer=template_answer,
    )
    version = getattr(rendered, "version", None)
    return (
        rendered.text,
        f"travel.reporter@{version}" if version is not None else "",
        str(getattr(rendered, "source", "") or ""),
    )


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
        if (item.get("status") != "success"
                or item.get("data_status") not in {
                    "available", "warmed_or_local_estimate",
                }):
            preview = []
        tasks.append({
            "task_id": item.get("task_id"),
            "type": item.get("type"),
            "status": item.get("status"),
            "data_status": item.get("data_status"),
            "result_count": item.get("result_count"),
            "preview": preview[:4] if isinstance(preview, list) else [],
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
    return facts


def _validate_reply(reply: str, evidence_text: str) -> tuple[str, str]:
    """阻止 Reporter 新造数字、实时数据状态或行程已生效承诺。"""
    text = (reply or "").strip()
    if not text:
        return "", "empty"
    if len(text) > _MAX_REPLY_CHARS:
        return "", "too_long"
    if "\n" in text or "\r" in text or "```" in text or "http://" in text or "https://" in text:
        return "", "forbidden_content"
    if any(marker in text for marker in _FORBIDDEN_CLAIMS):
        return "", "forbidden_claim"
    evidence_tokens = {
        match.group(0).casefold()
        for match in _NUMBER_TOKEN.finditer(evidence_text)
    }
    for match in _NUMBER_TOKEN.finditer(text):
        if match.group(0).casefold() not in evidence_tokens:
            return "", "unsupported_numeric_claim"
    for marker in _EVIDENCE_MARKERS:
        if marker in text and marker not in evidence_text:
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
        facts_json = json.dumps(verified_facts, ensure_ascii=False, default=str)
        try:
            system_prompt, prompt_version, prompt_source = _render_prompt(
                str(state.get("user_message") or "")[:500],
                facts_json,
                (template_answer or "")[:3000],
            )
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
        human_payload = json.dumps({
            "verified_facts": verified_facts,
            "template_answer": (template_answer or "")[:3000],
        }, ensure_ascii=False, default=str)
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
