"""State canonical readers 与旧字段兼容投影。

生产消费方优先读取本模块提供的 canonical accessor；旧字段只作为恢复存量
checkpoint 与兼容输出的 fallback，不在这里反向覆盖 canonical state。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from backend.orchestration.router.projection import (
    build_route_decision_v2_from_route,
    project_route_decision_to_legacy_state,
)
from backend.orchestration.router.types import RouteDecisionV2


def canonical_route_decision(state: Mapping[str, Any]) -> dict[str, Any]:
    """读取 RouteDecisionV2；老 checkpoint 才按旧 route_mode 构造 fallback。"""

    value = state.get("route_decision_v2")
    if isinstance(value, Mapping) and value:
        return dict(value)
    legacy = state.get("route_decision")
    legacy_mapping = legacy if isinstance(legacy, Mapping) else {}
    route_mode = str(
        state.get("route_mode")
        or legacy_mapping.get("route_mode")
        or "plan"
    )
    meta = legacy_mapping.get("routing_meta")
    meta = meta if isinstance(meta, Mapping) else {}
    return build_route_decision_v2_from_route(
        route_mode,
        domain=meta.get("domain"),
        subflow=meta.get("subflow"),
        workflow_name=legacy_mapping.get("workflow_name"),
        confidence=float(legacy_mapping.get("confidence") or 0.0),
        reason="legacy state fallback",
    ).model_dump(mode="json")


def legacy_route_decision(state: Mapping[str, Any]) -> dict[str, Any]:
    """读取旧 RouteDecision；仅在旧字段缺失时从 canonical V2 投影。"""

    value = state.get("route_decision")
    if isinstance(value, Mapping) and value:
        return dict(value)
    decision = RouteDecisionV2.model_validate(canonical_route_decision(state))
    return project_route_decision_to_legacy_state(
        decision,
        legacy_route_mode=str(state.get("route_mode") or "") or None,
    )["route_decision"]


def resolved_params(state: Mapping[str, Any]) -> dict[str, Any]:
    """读取执行参数；resolved_params 是事实源，tool_arguments 只做兼容 fallback。"""

    value = state.get("resolved_params")
    if isinstance(value, Mapping):
        return dict(value)
    legacy = state.get("tool_arguments")
    if isinstance(legacy, Mapping):
        return dict(legacy)
    return {}


def clarification_request(state: Mapping[str, Any]) -> dict[str, Any] | None:
    """读取结构化澄清请求；旧 _clarify/平铺字段只做兼容 fallback。"""

    value = state.get("clarification_request")
    if isinstance(value, Mapping) and value:
        return dict(value)
    legacy = state.get("_clarify")
    if isinstance(legacy, Mapping) and legacy:
        return dict(legacy)
    if state.get("need_clarification"):
        return {
            "question": "请补充一下您的需求：",
            "source": str(state.get("clarification_reason") or ""),
        }
    return None
