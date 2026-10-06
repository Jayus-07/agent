"""Router 决策到现有 Trace metadata 的最小投影。

只记录低基数、非用户输入的路由结果；不创建 trace、不写 state，也不影响
Router 的路由结果。
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from backend.shared.logger import logger


RUNTIME_TRACE_FIELDS = (
    "domain",
    "subflow",
    "runtime_type",
    "runtime_id",
    "interaction_mode",
    "execution_mode",
    "workflow_id",
    "capability",
    "skill_id",
    "tool_id",
    "prompt_version",
    "confidence",
    "source",
)


def record_router_decision(
    domain_decision: Mapping[str, Any],
    capability_decision: Mapping[str, Any],
    execution_decision: Mapping[str, Any],
    intent_decision: Mapping[str, Any] | None = None,
    route_decision_v2: Any | None = None,
) -> None:
    """将安全的 Router 决策摘要写入本请求的已有 trace。"""
    try:
        from backend.observability.tracer import trace_collector

        trace = trace_collector.current()
        if trace is None:
            return

        capability_confidence = _confidence(capability_decision.get("confidence"))
        domain_confidence = _confidence(domain_decision.get("confidence"))
        router_metadata = {
            "domain": _label(domain_decision.get("domain")),
            "subflow": _label(domain_decision.get("subflow")),
            "capability": _label(capability_decision.get("capability")),
            "mode": _label(execution_decision.get("mode")),
            "confidence": (
                capability_confidence
                if capability_confidence is not None
                else domain_confidence
            ),
            "source": _label(
                capability_decision.get("source")
                or domain_decision.get("source")
            ),
        }
        if intent_decision is not None:
            router_metadata.update({
                "intent": _label(intent_decision.get("intent")),
                "intent_kind": _label(intent_decision.get("kind")),
                "intent_source": _label(intent_decision.get("source")),
                "intent_confidence": _confidence(intent_decision.get("confidence")),
            })
        trace.metadata["router"] = router_metadata
        runtime = _runtime_fields(
            domain_decision=domain_decision,
            capability_decision=capability_decision,
            execution_decision=execution_decision,
            intent_decision=intent_decision,
            route_decision_v2=route_decision_v2,
            trace=trace,
        )
        _merge_runtime_fields(trace, runtime)
    except Exception:
        logger.debug("[RouterTrace] 路由决策 metadata 写入失败", exc_info=True)


def record_runtime_attribution(
    *,
    skill_id: str | None = None,
    tool_id: str | None = None,
    agent_domain: str | None = None,
) -> None:
    """把执行栈归因补到当前 Trace；不参与业务决策。"""

    try:
        from backend.observability.tracer import trace_collector

        trace = trace_collector.current()
        if trace is None:
            return
        _merge_runtime_fields(trace, {
            "skill_id": _label(skill_id),
            "tool_id": _label(tool_id),
            "domain": _label(agent_domain),
        })
    except Exception:
        logger.debug("[RouterTrace] 执行归因写入失败", exc_info=True)


def _runtime_fields(
    *,
    domain_decision: Mapping[str, Any],
    capability_decision: Mapping[str, Any],
    execution_decision: Mapping[str, Any],
    intent_decision: Mapping[str, Any] | None,
    route_decision_v2: Any | None,
    trace: Any,
) -> dict[str, Any]:
    """以 V2 为主、旧决策为兼容回退，构造固定 Trace 维度。"""

    if hasattr(route_decision_v2, "model_dump"):
        route_decision_v2 = route_decision_v2.model_dump(mode="json")
    v2 = route_decision_v2 if isinstance(route_decision_v2, Mapping) else {}
    domain = v2.get("domain") if isinstance(v2.get("domain"), Mapping) else {}
    runtime = v2.get("runtime") if isinstance(v2.get("runtime"), Mapping) else {}
    interaction = v2.get("interaction") if isinstance(v2.get("interaction"), Mapping) else {}
    execution = v2.get("execution") if isinstance(v2.get("execution"), Mapping) else {}
    capability = v2.get("capability") if isinstance(v2.get("capability"), Mapping) else {}

    runtime_type = _label(runtime.get("type"))
    runtime_id = _label(runtime.get("id"))
    workflow_id = _label(v2.get("workflow_name"))
    if not workflow_id and runtime_type == "workflow_runtime":
        workflow_id = runtime_id
    prompt_version = _label(
        getattr(trace, "tags", {}).get("prompt_version")
        or getattr(trace, "tags", {}).get("prompt_versions")
    )
    return {
        "domain": _label(domain.get("name") or domain_decision.get("domain")),
        "subflow": _label(domain.get("subflow") or domain_decision.get("subflow")),
        "runtime_type": runtime_type,
        "runtime_id": runtime_id,
        "interaction_mode": _label(interaction.get("mode")),
        "execution_mode": _label(execution.get("mode") or execution_decision.get("mode")),
        "workflow_id": workflow_id,
        "capability": _label(
            capability.get("name") or capability_decision.get("capability")
        ),
        "skill_id": None,
        "tool_id": None,
        "prompt_version": prompt_version,
        "confidence": _confidence(
            capability.get("confidence")
            or capability_decision.get("confidence")
            or domain.get("confidence")
            or domain_decision.get("confidence")
        ),
        "source": _label(
            capability.get("source")
            or capability_decision.get("source")
            or domain.get("source")
            or domain_decision.get("source")
            or (intent_decision or {}).get("source")
        ),
    }


def _merge_runtime_fields(trace: Any, fields: Mapping[str, Any]) -> None:
    runtime = {
        field: None for field in RUNTIME_TRACE_FIELDS
    }
    runtime.update(trace.metadata.get("runtime") or {})
    for field in RUNTIME_TRACE_FIELDS:
        value = fields.get(field)
        if value is None or value == "":
            continue
        runtime[field] = value
        trace.tags[f"runtime_{field}"] = value
    trace.metadata["runtime"] = runtime


def _label(value: Any) -> str | None:
    """将枚举型决策值限制为短字符串，拒绝对象、列表及用户文本。"""
    if not isinstance(value, str):
        return None
    label = value.strip()
    return label[:128] if label else None


def _confidence(value: Any) -> float | None:
    """仅保留有限的数值置信度，避免 NaN/Inf 破坏 JSON 序列化。"""
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(confidence):
        return None
    return round(confidence, 3)


__all__ = [
    "RUNTIME_TRACE_FIELDS",
    "record_router_decision",
    "record_runtime_attribution",
]
