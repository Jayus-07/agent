"""旅游单轮统一语义决策服务。

LLM 只产生 TravelTurnDecision 候选；业务字段仍由确定性校验和 slot_filler
合并，图节点与工具执行权均不交给模型。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from backend.shared.logger import logger
from backend.travel.core.intent import TravelTurnDecision


@dataclass
class TurnDecisionOutcome:
    decision: TravelTurnDecision | None = None
    status: str = "disabled"
    fallback_reason: str = "disabled"
    model: str = ""
    prompt_version: str = ""
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def meta(self) -> dict[str, Any]:
        """仅导出追踪所需标量，不包含原文、槽位值或模型响应。"""
        return {
            "status": self.status,
            "fallback_reason": self.fallback_reason,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "has_decision": self.decision is not None,
        }


def _render_prompt() -> tuple[str, str]:
    try:
        from backend.prompts.service import prompt_service

        rendered = prompt_service.render_sync("travel.turn_decision")
        version = getattr(rendered, "version", None)
        label = f"travel.turn_decision@{version}" if version else ""
        return rendered.text, label
    except Exception as exc:  # noqa: BLE001 — Prompt 不可用时安全回退
        logger.warning("[TravelTurnDecision] Prompt 渲染失败，使用内置模板: %s", exc)
        return _SYSTEM_PROMPT, ""


_SYSTEM_PROMPT = """你是旅游助手的单轮需求理解器。只输出符合 TravelTurnDecision 契约的 JSON；不执行工具、不生成行程、不声称修改或应用成功。
primary_action 只能是 answer、discover、create_plan、modify_plan、replan_plan。
changes 只允许 set_pace、set_days、set_budget、set_destination、set_preferences、add_poi、remove_poi、replace_poi、set_weather_condition。
additional_tasks 只允许 query_train、query_weather、query_poi、query_hotel，参数只允许 origin、destination、city、travel_date、day_index。
brief_candidates 只允许 destination、days、party_size、pace、preferences，并必须包含置信度和原文证据。不得输出契约外字段。普通问答不得修改行程。无法确认修改目标时必须追问，不得猜测。"""


def interpret_turn_with_llm(
    message: str,
    *,
    context: dict[str, Any],
    timeout_ms: int | None = None,
) -> TurnDecisionOutcome:
    """单次 Structured Output 调用，异常/非法结果统一返回空决策。"""
    from backend.config import model_roles
    from backend.config.travel import (
        TRAVEL_TURN_DECISION_LLM_ENABLED,
        TRAVEL_TURN_DECISION_TIMEOUT_MS,
    )

    outcome = TurnDecisionOutcome()
    if not TRAVEL_TURN_DECISION_LLM_ENABLED:
        return outcome
    if not (message or "").strip():
        outcome.status = "empty"
        outcome.fallback_reason = "empty_message"
        return outcome

    started = time.monotonic()
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        from backend.infra.async_utils import sync_call_with_timeout
        from backend.infra.llm.proxy import _build_llm_for, record_llm_result
        from backend.observability.llm_context import llm_attribution_scope

        outcome.prompt_version = ""
        system_prompt, outcome.prompt_version = _render_prompt()
        model_config = model_roles.resolve_effective("main") or {}
        model_name = str(model_config.get("value") or "")
        outcome.model = model_name
        if not model_name:
            outcome.status = "error"
            outcome.fallback_reason = "no_model"
            return outcome

        llm = _build_llm_for(model_name).bind(temperature=0, max_tokens=1400)
        human_payload = json.dumps(
            {"user_message": message, "verified_context": context or {}},
            ensure_ascii=False,
            default=str,
        )
        timeout = (timeout_ms or TRAVEL_TURN_DECISION_TIMEOUT_MS) / 1000.0
        with llm_attribution_scope(agent_domain="travel"):
            response = sync_call_with_timeout(
                llm.invoke,
                timeout,
                [SystemMessage(content=system_prompt),
                 HumanMessage(content=human_payload)],
            )
            try:
                record_llm_result(
                    response,
                    duration_ms=(time.monotonic() - started) * 1000,
                    model_name=model_name,
                )
            except Exception as exc:  # noqa: BLE001 — 计量故障不丢弃有效决策
                logger.warning("[TravelTurnDecision] Token 计量补账失败: %s", exc)

        usage = getattr(response, "usage_metadata", None) or {}
        response_metadata = getattr(response, "response_metadata", None) or {}
        if not isinstance(usage, dict):
            usage = {}
        if not usage and isinstance(response_metadata, dict):
            usage = response_metadata.get("token_usage") or {}
        if isinstance(usage, dict):
            try:
                outcome.input_tokens = int(
                    usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0)
                outcome.output_tokens = int(
                    usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0)
            except (TypeError, ValueError):
                outcome.input_tokens = 0
                outcome.output_tokens = 0

        raw = str(getattr(response, "content", "") or "")
        from backend.shared.json_extractor import extract_json

        payload = extract_json(raw, source="travel.turn_decision")
        if not isinstance(payload, dict):
            outcome.status = "schema_invalid"
            outcome.fallback_reason = "invalid_json"
        else:
            decision = TravelTurnDecision.model_validate(payload)
            outcome.decision = decision.model_copy(update={"parse_source": "llm"})
            outcome.status = "ok"
            outcome.fallback_reason = ""
    except ValidationError as exc:
        outcome.status = "schema_invalid"
        outcome.fallback_reason = "schema_validation_failed"
        logger.info("[TravelTurnDecision] Structured Output 校验失败: %s", exc)
    except Exception as exc:  # noqa: BLE001 — 超时/模型异常不得改写业务状态
        outcome.status = "error"
        outcome.fallback_reason = "llm_error"
        logger.info(
            "[TravelTurnDecision] 模型理解失败，转规则/追问: %s: %s",
            type(exc).__name__, str(exc)[:120],
        )
    finally:
        outcome.latency_ms = int((time.monotonic() - started) * 1000)
    return outcome
