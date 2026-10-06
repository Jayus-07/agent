"""从 capabilities.yaml 派生 ToolSpec V2，禁止运行时散落第二份配置。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from backend.core.tool_governance.models import (
    ApprovalPolicy,
    Operation,
    RiskLevel,
    ToolSpec,
)
from backend.core.tool_governance.schema_validator import legacy_params_to_schema


_MANIFEST_PATH = Path(__file__).resolve().parents[2] / "orchestration" / "router" / "capabilities.yaml"
_VALID_OPERATIONS = {item.value for item in Operation}
_VALID_RISKS = {item.value for item in RiskLevel}
_VALID_APPROVALS = {item.value for item in ApprovalPolicy}


class ToolSpecRegistryError(RuntimeError):
    """ToolSpec 缺失或非法，启动期必须 fail-fast。"""


def _tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _parse_spec(item: dict[str, Any]) -> ToolSpec:
    raw = item.get("tool_spec")
    if not isinstance(raw, dict):
        raise ToolSpecRegistryError(f"{item.get('name')}: 缺少完整 tool_spec")
    required = ("version", "operation", "risk_level", "confirmation_policy")
    missing = [key for key in required if key not in raw]
    if missing:
        raise ToolSpecRegistryError(f"{item.get('name')}: tool_spec 缺少 {missing}")
    operation = str(raw["operation"])
    risk_level = str(raw["risk_level"])
    approval = str(raw["confirmation_policy"])
    if operation not in _VALID_OPERATIONS:
        raise ToolSpecRegistryError(f"{item['name']}: operation 非法: {operation}")
    if risk_level not in _VALID_RISKS:
        raise ToolSpecRegistryError(f"{item['name']}: risk_level 非法: {risk_level}")
    if approval not in _VALID_APPROVALS:
        raise ToolSpecRegistryError(f"{item['name']}: confirmation_policy 非法: {approval}")
    params = item.get("params_schema") or {}
    runtime = raw.get("runtime") or {}
    retry = raw.get("retry") or {}
    dedupe = raw.get("dedupe") or {}
    rate_limit = raw.get("rate_limit") or {}
    breaker = raw.get("circuit_breaker") or {}
    idem = raw.get("idempotency") or {}
    output = raw.get("output_schema") or {
        "nullable": True,
        "type": ["object", "array", "string", "number", "boolean", "null"],
        "additionalProperties": True,
    }
    operation_by = {
        str(key): Operation(str(value))
        for key, value in (raw.get("operation_by") or {}).items()
    }
    risk_by = {
        str(key): RiskLevel(str(value))
        for key, value in (raw.get("risk_by") or {}).items()
    }
    confirmation_by = {
        str(key): ApprovalPolicy(str(value))
        for key, value in (raw.get("confirmation_by") or {}).items()
    }
    return ToolSpec(
        capability=str(item["name"]),
        version=int(raw["version"]),
        domains=_tuple(item.get("domains") or item.get("domain")),
        description=str(item.get("description") or ""),
        params_schema=(
            legacy_params_to_schema(params, include_auto=True)
            if not (isinstance(params, dict) and params.get("type") == "object")
            else {
                **params,
                "additionalProperties": params.get("additionalProperties", False),
            }
        ),
        output_schema=output,
        operation=Operation(operation),
        risk_level=RiskLevel(risk_level),
        confirmation_policy=ApprovalPolicy(approval),
        operation_by=operation_by,
        risk_by=risk_by,
        confirmation_by=confirmation_by,
        required_scopes=_tuple(raw.get("required_scopes")),
        required_roles=_tuple(raw.get("required_roles")),
        timeout_ms=int(runtime.get("timeout_ms", 15_000)),
        max_calls_per_request=int(runtime.get("max_calls_per_request", 3)),
        retry_max_attempts=int(retry.get("max_attempts", 0)),
        dedupe_enabled=bool(dedupe.get("enabled", True)),
        dedupe_ttl_seconds=int(dedupe.get("ttl_seconds", 30)),
        rate_limit_enabled=bool(rate_limit.get("enabled", False)),
        rate_limit_requests=int(rate_limit.get("requests", 30)),
        rate_limit_window_seconds=int(rate_limit.get("window_seconds", 60)),
        rate_limit_scope=str(rate_limit.get("scope", "tenant")),
        circuit_breaker_enabled=bool(breaker.get("enabled", True)),
        circuit_failure_threshold=int(breaker.get("failure_threshold", 5)),
        circuit_cooldown_seconds=float(breaker.get("cooldown_seconds", 30)),
        idempotency_required=bool(idem.get("required", False)),
        audit_policy=dict(raw.get("audit") or {}),
        auto_params=tuple(
            name for name, value in params.items()
            if isinstance(value, dict) and value.get("auto")
        ),
    )


@lru_cache(maxsize=1)
def all_tool_specs() -> dict[str, ToolSpec]:
    raw = yaml.safe_load(_MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ToolSpecRegistryError("capabilities.yaml 顶层必须是 object")
    specs = {}
    for item in raw.get("capabilities") or []:
        if not isinstance(item, dict):
            raise ToolSpecRegistryError("capabilities 项必须是 object")
        spec = _parse_spec(item)
        if spec.capability in specs:
            raise ToolSpecRegistryError(f"重复 capability: {spec.capability}")
        specs[spec.capability] = spec
    for item in raw.get("tool_specs") or []:
        if not isinstance(item, dict):
            raise ToolSpecRegistryError("tool_specs 项必须是 object")
        if not item.get("name"):
            raise ToolSpecRegistryError("tool_specs 项缺少 name")
        spec = _parse_spec({**item, "tool_spec": item.get("tool_spec") or item.get("governance")})
        if spec.capability in specs:
            raise ToolSpecRegistryError(f"重复 ToolSpec: {spec.capability}")
        specs[spec.capability] = spec
    return specs


def get_tool_spec(capability: str) -> ToolSpec | None:
    return all_tool_specs().get(capability)


def validate_tool_spec_registry() -> dict[str, Any]:
    specs = all_tool_specs()
    raw = yaml.safe_load(_MANIFEST_PATH.read_text(encoding="utf-8"))
    capability_count = len(raw.get("capabilities") or [])
    failures: list[str] = []
    for name, spec in specs.items():
        if spec.version != 2:
            failures.append(f"{name}: version != 2")
        if not spec.params_schema.get("additionalProperties") is False:
            failures.append(f"{name}: params_schema 未默认拒绝未知参数")
        if spec.risk_level in (RiskLevel.R2, RiskLevel.R3) and not spec.requires_confirmation:
            failures.append(f"{name}: 高风险缺少用户确认")
        for operation, risk in spec.risk_by.items():
            policy = spec.confirmation_by.get(operation, spec.confirmation_policy)
            if risk in (RiskLevel.R2, RiskLevel.R3) and policy not in (
                ApprovalPolicy.USER_CONFIRMATION,
                ApprovalPolicy.DUAL,
            ):
                failures.append(f"{name}:{operation}: 动态高风险缺少用户确认")
    if failures:
        raise ToolSpecRegistryError("; ".join(failures))
    return {
        "tool_registry_pass": True,
        "capability_spec_count": capability_count,
        "tool_spec_count": len(specs),
        "failures": [],
    }


__all__ = [
    "ToolSpecRegistryError",
    "all_tool_specs",
    "get_tool_spec",
    "validate_tool_spec_registry",
]
