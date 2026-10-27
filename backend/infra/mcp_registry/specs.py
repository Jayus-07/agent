"""mcp_registry.specs — DB 行 → 治理契约派生（ToolSpec / CapabilityDecl）。

上游 MCP server 声明的工具名、描述与 Schema 全部是**不可信输入**：
  - content_hash 固定 description + inputSchema（+ outputSchema），
    审核通过后上游任何变化都会使 approved_hash 失配 → 待重审；
  - 描述进入 Planner/FC prompt 前有长度截断，防 prompt 注入面扩大；
  - capability slug 经白名单归一，不透传上游原始命名。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from backend.core.tool_governance.models import (
    ApprovalPolicy,
    Operation,
    RiskLevel,
    ToolSpec,
)
from backend.orchestration.router.manifest import CapabilityDecl

# manifest 校验的 capability 两段正则（mcp.<slug> 的 slug 侧约束）
_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# 上游工具名常见合法字符（- 与 .），统一折叠为下划线
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9_]+")
# invocation 模板占位符：{name}
_TEMPLATE_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

MAX_DESCRIPTION_CHARS = 500


def slugify(text: str, *, max_length: int = 48) -> str:
    """任意上游命名 → 平台 slug（小写字母数字下划线，数字不开头）。"""
    lowered = str(text or "").strip().lower()
    folded = _SLUG_STRIP_RE.sub("_", lowered).strip("_")
    folded = re.sub(r"_+", "_", folded) or "tool"
    if folded[0].isdigit():
        folded = f"t{folded}"
    return folded[:max_length].rstrip("_") or "tool"


def capability_slug(conn_name: str, upstream_tool: str) -> str:
    """`<连接名>__<上游工具名>` 的全局唯一 slug（capability 名 = mcp.<slug>）。"""
    return f"{slugify(conn_name, max_length=24)}__{slugify(upstream_tool, max_length=48)}"


def is_valid_slug(slug: str) -> bool:
    return bool(_SLUG_RE.match(slug or ""))


def content_hash(description: str, input_schema: dict | None,
                 output_schema: dict | None = None,
                 invocation: dict | None = None) -> str:
    """发现内容的稳定摘要（审核绑定 + 漂移检测）。

    HTTP API 工具的调用定义（invocation）参与摘要——调用目标/方法/参数
    映射变化等同契约变化，必须重审。
    """
    canonical = json.dumps(
        {
            "description": str(description or ""),
            "input_schema": input_schema or {},
            "output_schema": output_schema or {},
            "invocation": invocation or {},
        },
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def normalize_input_schema(schema: dict | None) -> dict[str, Any]:
    """上游 inputSchema → 平台统一的 JSON Schema object（默认拒绝未知参数）。"""
    normalized = dict(schema or {})
    if normalized.get("type") != "object":
        normalized["type"] = "object"
    properties = normalized.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    normalized["properties"] = {
        str(name): (value if isinstance(value, dict) else {"type": "string"})
        for name, value in properties.items()
    }
    normalized["additionalProperties"] = False
    return normalized


class SpecDeriveError(ValueError):
    """治理配置非法，工具不能进入运行时快照（fail-fast，逐工具隔离）。"""


_VALID_OPERATIONS = {item.value for item in Operation}
_VALID_RISKS = {item.value for item in RiskLevel}
_VALID_APPROVALS = {item.value for item in ApprovalPolicy}

# HTTP API 工具调用定义的白名单（Batch 1 第一批）
_VALID_HTTP_METHODS = {"GET", "POST"}
_MAX_PATH_TEMPLATES = 8

# Authorization 系列头由连接凭据统一注入，配置面不可覆盖（防提权/防凭证泄露）
_FORBIDDEN_HEADER_NAMES = {"authorization", "proxy-authorization", "cookie"}


def validate_invocation(invocation: dict | None,
                        input_schema: dict | None) -> dict[str, Any]:
    """HTTP API 工具的受控调用定义校验（fail-fast）。

    结构：{method, path, query?, headers?, body_from?}
      - method ∈ {GET, POST}；path 为相对路径（/ 开头，禁 scheme/host）；
      - path/query/headers 里的 {name} 模板引用 input_schema.properties 中
        声明的参数——未声明即拒绝（不出网发现不了，登记时就拦）；
      - headers 禁止 Authorization/Cookie（凭据注入专用）；
      - POST 可用 body_from 指定整体作为 JSON body 的参数名。
    返回归一后的 invocation；非法抛 SpecDeriveError。
    """
    raw = dict(invocation or {})
    if not raw:
        return {}
    method = str(raw.get("method") or "").upper()
    if method not in _VALID_HTTP_METHODS:
        raise SpecDeriveError(
            f"invocation.method 非法: {method!r}（允许 {_VALID_HTTP_METHODS}）")
    path = str(raw.get("path") or "")
    if not path.startswith("/") or "://" in path or path.startswith("//"):
        raise SpecDeriveError(
            f"invocation.path 必须是以 / 开头的相对路径: {path!r}")

    properties = (input_schema or {}).get("properties") or {}
    declared = set(properties) if isinstance(properties, dict) else set()

    def _check_templates(section: str, mapping: Any) -> dict[str, str]:
        if mapping is None:
            return {}
        if not isinstance(mapping, dict):
            raise SpecDeriveError(f"invocation.{section} 必须是对象")
        out: dict[str, str] = {}
        for key, value in mapping.items():
            str_key, str_value = str(key), str(value or "")
            if section == "headers" and str_key.lower() in _FORBIDDEN_HEADER_NAMES:
                raise SpecDeriveError(
                    f"invocation.headers 禁止配置 {str_key}（凭据统一注入）")
            for placeholder in _TEMPLATE_RE.findall(str_value):
                if placeholder not in declared:
                    raise SpecDeriveError(
                        f"invocation.{section}.{key} 引用未声明参数: "
                        f"{{{placeholder}}}")
            for placeholder in _TEMPLATE_RE.findall(str_key):
                if placeholder not in declared:
                    raise SpecDeriveError(
                        f"invocation.{section} 键引用未声明参数: "
                        f"{{{placeholder}}}")
            out[str_key] = str_value
        return out

    query = _check_templates("query", raw.get("query"))
    headers = _check_templates("headers", raw.get("headers"))

    path_templates = _TEMPLATE_RE.findall(path)
    if len(path_templates) > _MAX_PATH_TEMPLATES:
        raise SpecDeriveError(
            f"invocation.path 模板参数过多（>{_MAX_PATH_TEMPLATES}）")
    for placeholder in path_templates:
        if placeholder not in declared:
            raise SpecDeriveError(
                f"invocation.path 引用未声明参数: {{{placeholder}}}")

    body_from = str(raw.get("body_from") or "")
    if body_from:
        if method != "POST":
            raise SpecDeriveError("invocation.body_from 仅 POST 支持")
        if body_from not in declared:
            raise SpecDeriveError(
                f"invocation.body_from 引用未声明参数: {body_from}")

    normalized: dict[str, Any] = {
        "method": method,
        "path": path,
        "query": query,
        "headers": headers,
    }
    if body_from:
        normalized["body_from"] = body_from
    return normalized


def derive_tool_spec(
    capability: str,
    description: str,
    input_schema: dict | None,
    governance: dict | None,
) -> ToolSpec:
    """审核治理配置（mcp_tools.governance JSONB）→ ToolSpec。

    治理字段与 capabilities.yaml 的 tool_spec.runtime/retry/... 同构；
    非法值抛 SpecDeriveError，调用方跳过该工具并记录错误状态。
    """
    raw = dict(governance or {})
    operation = str(raw.get("operation") or "read")
    risk_level = str(raw.get("risk_level") or "R1")
    approval = str(raw.get("confirmation_policy") or "none")
    if operation not in _VALID_OPERATIONS:
        raise SpecDeriveError(f"{capability}: operation 非法: {operation}")
    if risk_level not in _VALID_RISKS:
        raise SpecDeriveError(f"{capability}: risk_level 非法: {risk_level}")
    if approval not in _VALID_APPROVALS:
        raise SpecDeriveError(f"{capability}: confirmation_policy 非法: {approval}")
    # 与静态注册表同等安全底线：高风险必须带用户确认
    if risk_level in ("R2", "R3") and approval not in (
        ApprovalPolicy.USER_CONFIRMATION.value, ApprovalPolicy.DUAL.value,
    ):
        raise SpecDeriveError(f"{capability}: R2/R3 必须声明用户确认")

    runtime = raw.get("runtime") or {}
    retry = raw.get("retry") or {}
    dedupe = raw.get("dedupe") or {}
    rate_limit = raw.get("rate_limit") or {}
    breaker = raw.get("circuit_breaker") or {}
    idem = raw.get("idempotency") or {}
    schema = normalize_input_schema(input_schema)
    # 写操作强制幂等要求与零重试（治理底线，审核者不可放开）
    is_write = operation != "read"
    retry_max = 0 if is_write else int(retry.get("max_attempts", 0))
    idempotency_required = bool(idem.get("required", False)) or is_write

    def _tuple(value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        return tuple(str(item) for item in value)

    return ToolSpec(
        capability=capability,
        version=2,
        domains=("mcp",),
        description=str(description or "")[:MAX_DESCRIPTION_CHARS],
        params_schema=schema,
        output_schema={
            "nullable": True,
            "type": ["object", "array", "string", "number", "boolean", "null"],
            "additionalProperties": True,
        },
        operation=Operation(operation),
        risk_level=RiskLevel(risk_level),
        confirmation_policy=ApprovalPolicy(approval),
        required_scopes=_tuple(raw.get("required_scopes")),
        required_roles=_tuple(raw.get("required_roles")),
        timeout_ms=int(runtime.get("timeout_ms", 15_000)),
        max_calls_per_request=int(runtime.get("max_calls_per_request", 3)),
        retry_max_attempts=retry_max,
        dedupe_enabled=bool(dedupe.get("enabled", True)),
        dedupe_ttl_seconds=int(dedupe.get("ttl_seconds", 30)),
        rate_limit_enabled=bool(rate_limit.get("enabled", False)),
        rate_limit_requests=int(rate_limit.get("requests", 30)),
        rate_limit_window_seconds=int(rate_limit.get("window_seconds", 60)),
        rate_limit_scope=str(rate_limit.get("scope", "tenant")),
        circuit_breaker_enabled=bool(breaker.get("enabled", True)),
        circuit_failure_threshold=int(breaker.get("failure_threshold", 5)),
        circuit_cooldown_seconds=float(breaker.get("cooldown_seconds", 30)),
        idempotency_required=idempotency_required,
        auto_params=(),
    )


def derive_capability_decl(
    capability: str,
    description: str,
    input_schema: dict | None,
    governance: dict | None,
    examples: list | None,
) -> CapabilityDecl:
    """派生路由声明。examples ≥ 2 才 routed（与静态 manifest 同规则）；
    不足时 routed=False 且给 reason——工具仍可被治理测试调用，但不可被
    Agent 路由选中（fail-safe）。planner_example 由审核表单存于
    governance.planner_example（dict），缺省给占位示例。
    """
    raw = dict(governance or {})
    schema = normalize_input_schema(input_schema)
    routed_examples = [
        str(item).strip() for item in (examples or []) if str(item or "").strip()
    ]
    routed = len(routed_examples) >= 2
    planner_example = raw.get("planner_example")
    if not isinstance(planner_example, dict):
        planner_example = {}
    risk_level = str(raw.get("risk_level") or "R1").upper()
    return CapabilityDecl(
        name=capability,
        skill="mcp_dynamic",
        routed=routed,
        description=str(description or "")[:MAX_DESCRIPTION_CHARS],
        params_schema=dict(schema.get("properties") or {}),
        planner_examples=(dict(planner_example),),
        examples=tuple(routed_examples),
        reason="" if routed else "缺少至少 2 条审核路由示例，暂不进入路由候选",
        domain="mcp",
        risk_level="HIGH" if risk_level == "R3" else ("MEDIUM" if risk_level == "R2" else "LOW"),
        fast_path_enabled=False,
    )


__all__ = [
    "MAX_DESCRIPTION_CHARS",
    "SpecDeriveError",
    "capability_slug",
    "content_hash",
    "derive_capability_decl",
    "derive_tool_spec",
    "is_valid_slug",
    "normalize_input_schema",
    "slugify",
    "validate_invocation",
]
