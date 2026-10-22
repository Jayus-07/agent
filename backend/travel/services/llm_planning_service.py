"""基于受控候选事实生成旅游逐日规划建议。

本服务只让 LLM 对给定 POI 的逐日归属做建议，不执行 Provider/Tool、不写 Trip，
也不决定授权或硬约束。候选 ID、日容量和必去项由 PlanningAgent 与 poi_service
在调用后的确定性边界再次校验。
"""
from __future__ import annotations

import contextvars
import json
import time
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.shared.logger import logger


class TravelDayPlanProposal(BaseModel):
    """模型建议的按日 POI ID 分组；不接受自然语言执行指令或扩展字段。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    days: list[list[str]] = Field(min_length=1, max_length=15)
    dropped_poi_ids: list[str] = Field(default_factory=list, max_length=120)


@dataclass
class PlanningProposalOutcome:
    proposal: TravelDayPlanProposal | None = None
    status: str = "disabled"
    fallback_reason: str = "disabled"
    model: str = ""
    prompt_version: str = ""
    latency_ms: int = 0
    candidate_count: int = 0

    def meta(self) -> dict[str, Any]:
        """可进入图状态与 Trace 的标量元数据，不含用户偏好或模型原文。"""
        return {
            "status": self.status,
            "fallback_reason": self.fallback_reason,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "latency_ms": self.latency_ms,
            "candidate_count": self.candidate_count,
            "has_proposal": self.proposal is not None,
        }


def propose_day_plan(context: dict[str, Any]) -> PlanningProposalOutcome:
    """一次受治理的 LLM 调用，失败时返回空建议供规则 Planner 回退。"""
    from backend.config import model_roles
    from backend.config.travel import (
        TRAVEL_PLANNING_LLM_ENABLED,
        TRAVEL_PLANNING_LLM_MAX_CANDIDATES,
        TRAVEL_PLANNING_LLM_MAX_TOKENS,
        TRAVEL_PLANNING_LLM_TIMEOUT_MS,
    )

    candidates = context.get("candidates")
    count = len(candidates) if isinstance(candidates, list) else 0
    outcome = PlanningProposalOutcome(candidate_count=count)
    if not TRAVEL_PLANNING_LLM_ENABLED:
        return outcome
    if not candidates:
        outcome.status = "skipped"
        outcome.fallback_reason = "no_candidates"
        return outcome
    if count > TRAVEL_PLANNING_LLM_MAX_CANDIDATES:
        outcome.status = "skipped"
        outcome.fallback_reason = "candidate_limit_exceeded"
        return outcome

    started = time.monotonic()
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        from backend.infra.async_utils import sync_call_with_timeout
        from backend.infra.llm.proxy import bind_llm_for_role
        from backend.observability.llm_context import llm_attribution_scope
        from backend.prompts.service import prompt_service
        from backend.shared.json_extractor import extract_json

        rendered = prompt_service.render_sync("travel.planning_agent")
        version = getattr(rendered, "version", None)
        outcome.prompt_version = (
            f"travel.planning_agent@{version}"
            if version is not None else "travel.planning_agent@default"
        )
        model_config = model_roles.resolve_effective("main") or {}
        outcome.model = str(model_config.get("value") or "").strip()
        if not outcome.model:
            outcome.status = "error"
            outcome.fallback_reason = "no_model"
            return outcome

        llm = bind_llm_for_role(
            "main", temperature=0, max_tokens=TRAVEL_PLANNING_LLM_MAX_TOKENS,
        )
        payload = json.dumps(
            context, ensure_ascii=False, separators=(",", ":"), default=str,
        )
        messages = [
            SystemMessage(content=rendered.text),
            HumanMessage(content=payload),
        ]
        call_context = contextvars.copy_context()
        with llm_attribution_scope(agent_domain="travel"):
            response = sync_call_with_timeout(
                lambda: call_context.run(llm.invoke, messages),
                max(0.5, TRAVEL_PLANNING_LLM_TIMEOUT_MS / 1000),
            )

        raw = str(getattr(response, "content", "") or "")
        parsed = extract_json(raw, source="travel.planning_agent")
        if not isinstance(parsed, dict):
            outcome.status = "schema_invalid"
            outcome.fallback_reason = "invalid_json"
            return outcome
        outcome.proposal = TravelDayPlanProposal.model_validate(parsed)
        outcome.status = "ok"
        outcome.fallback_reason = ""
    except ValidationError:
        outcome.status = "schema_invalid"
        outcome.fallback_reason = "schema_validation_failed"
        logger.info("[TravelPlanning] LLM 日程建议不符合结构契约")
    except Exception as exc:  # noqa: BLE001 — 失败时交给确定性排程
        outcome.status = "error"
        outcome.fallback_reason = "llm_error"
        logger.info(
            "[TravelPlanning] LLM 不可用，回退规则排程：%s",
            type(exc).__name__,
        )
    finally:
        outcome.latency_ms = int((time.monotonic() - started) * 1000)
    return outcome


__all__ = [
    "PlanningProposalOutcome",
    "TravelDayPlanProposal",
    "propose_day_plan",
]
