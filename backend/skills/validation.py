"""Skill 执行边界的四层校验管线。

校验器只负责判断，不执行 Tool、不访问数据库；实际副作用仍由 Tool 自己
负责。所有失败都在 Skill 边界收口，避免把待审批、结构错误或业务失败结果
误标记为成功，也避免失败后继续走候选首项。
"""
from __future__ import annotations

import re
import json
from typing import Any, Callable

from backend.shared.error_protocol import ErrorCode, ProtocolError


class ValidationFailure(ProtocolError):
    """带校验层标识的安全失败。"""

    layer: str = ""

    def __init__(self, layer: str, code: ErrorCode, reason: str) -> None:
        self.layer = layer
        super().__init__(
            code,
            _safe_message(layer, code),
            source="skill.validation",
            details={"validation_layer": layer, "reason": reason},
        )


HIGH_RISK_CAPABILITIES = frozenset({
    "email.send",
    "data.export",
    "data.collect",
    "competitor.analyze",
    "competitor.watch",
})

_WRITE_ACTIONS = frozenset({"add", "remove", "toggle"})
_FAILURE_PREFIXES = (
    "[EXPORT FAILED]",
    "[AGENTLY ERROR:",
    "❌ 错误",
    "错误:",
)

# ``params_schema`` 只能表达单字段 required/type/enum，无法表达 action
# 分发后的跨字段契约。这里保留能力级矩阵，作为 Skill 进入 Tool 前的第二道
# 参数闸门；规则只读 params，不访问外部系统，避免把业务校验重新散落到各个
# Tool 实现中。
_MAP_ACTION_REQUIREMENTS: dict[str, tuple[tuple[str, ...], ...]] = {
    "weather": (("city", "location"),),
    "geocode": (("address", "keyword"),),
    "reverse_geocode": (("location",),),
    "place_search": (("keyword",),),
    "route": (("from_location",), ("to_location",)),
    "navigation": (("to_location",),),
    "static_map": (("location", "markers"),),
    "district": (("keyword", "district_id"),),
    "street_view": (("location",),),
}


def _present(params: dict[str, Any], name: str) -> bool:
    """判断参数是否有可执行值；数字 0 仍视为显式值。"""
    value = params.get(name)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _require_any(params: dict[str, Any], *names: str) -> str | None:
    if any(_present(params, name) for name in names):
        return None
    return f"至少提供一个参数: {', '.join(names)}"


def _require_all(params: dict[str, Any], *names: str) -> str | None:
    missing = [name for name in names if not _present(params, name)]
    if not missing:
        return None
    return f"缺少参数: {', '.join(missing)}"


def _validate_email(capability: str, params: dict[str, Any]) -> str | None:
    action = (params.get("action") or capability.rsplit(".", 1)[-1]).strip().lower()
    if action == "send":
        return _require_all(params, "to", "subject", "body")
    if action == "search":
        return _require_any(params, "query")
    if action == "read":
        return _require_all(params, "message_id")
    if action == "watch":
        timeout = params.get("timeout_sec")
        if timeout is not None and (not isinstance(timeout, int) or isinstance(timeout, bool)
                                    or timeout <= 0):
            return "timeout_sec 必须是大于 0 的整数"
    return None


def _validate_competitor(capability: str, params: dict[str, Any]) -> str | None:
    action = (params.get("action") or capability.rsplit(".", 1)[-1]).strip().lower()
    if action in {"analyze", "history"}:
        return _require_any(params, "url", "question")
    if action in _WRITE_ACTIONS:
        return _require_all(params, "url")
    return None


def _validate_map_lookup(_capability: str, params: dict[str, Any]) -> str | None:
    action = str(params.get("action") or "").strip().lower()
    requirements = _MAP_ACTION_REQUIREMENTS.get(action)
    if requirements is None:
        return "action 不是受支持的地图查询类型"
    for alternatives in requirements:
        error = _require_any(params, *alternatives)
        if error:
            return f"action={action} {error}"
    return None


def _validate_web_crawl(_capability: str, params: dict[str, Any]) -> str | None:
    url = str(params.get("url") or "").strip()
    if not re.match(r"^https?://[^\s]+$", url, flags=re.IGNORECASE):
        return "url 必须是 http:// 或 https:// 地址"
    return None


def _validate_positive_limit(_capability: str, params: dict[str, Any]) -> str | None:
    for name in ("limit", "num_results", "page_size"):
        if name not in params or params[name] is None:
            continue
        value = params[name]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return f"{name} 必须是大于 0 的整数"
    return None


CAPABILITY_VALIDATORS: dict[str, Callable[[str, dict[str, Any]], str | None]] = {
    "email.send": _validate_email,
    "email.search": _validate_email,
    "email.read": _validate_email,
    "email.watch": _validate_email,
    "competitor.analyze": _validate_competitor,
    "competitor.watch": _validate_competitor,
    "competitor.history": _validate_competitor,
    "map.lookup": _validate_map_lookup,
    "web.crawl": _validate_web_crawl,
    "web.search": _validate_positive_limit,
    "travel.poi_search": _validate_positive_limit,
}

_STRUCTURED_REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    "sql.query": frozenset({
        "sql", "tables", "columns", "rows", "row_count", "execution_time",
    }),
    "business.analyze": frozenset({
        "summary", "risks", "suggestions", "confidence", "related_knowledge",
    }),
    "travel.poi_search": frozenset({"city", "count", "pois"}),
}


def validate_capability_parameters(capability: str, params: dict[str, Any]) -> None:
    """执行 capability 级跨字段校验。

    ``BaseSkill`` 已完成声明式 schema 校验，这里只补充 action 分发、替代
    参数和数值边界。错误仍归类为 parameter，调用方可安全阻断而不触发副作用。
    """
    validator = CAPABILITY_VALIDATORS.get(capability)
    if validator is None:
        return
    error = validator(capability, params)
    if error:
        raise ValidationFailure("parameter", ErrorCode.INVALID_PARAM, error)


def _safe_message(layer: str, code: ErrorCode) -> str:
    if layer == "permission":
        return "当前身份无权执行该操作。"
    if layer == "output":
        return "工具返回结果不符合约定。"
    if layer == "semantic":
        return "工具结果与请求意图不匹配。"
    if code == ErrorCode.INVALID_PARAM:
        return "请求参数有误，请检查后重试。"
    return "工具校验失败，请稍后重试。"


def validate_invocation(
    capability: str,
    params: dict,
    parameter_validator: Callable[[dict], str | None],
) -> None:
    """执行参数层和权限层校验。"""
    param_error = parameter_validator(params)
    if param_error:
        raise ValidationFailure("parameter", ErrorCode.INVALID_PARAM, param_error)

    # 声明式 schema 通过后，再执行能力级跨字段契约；这一步仍发生在
    # Tool.invoke 之前，失败不会触发外部请求或写副作用。
    validate_capability_parameters(capability, params)

    # action 型 capability 的跨字段约束不能由 JSON schema 的单字段 required
    # 表达，在 Skill 边界补齐，避免 action=add 且 url 为空仍进入 Tool。
    action = params.get("action")
    if capability.startswith("competitor.") and action in _WRITE_ACTIONS:
        if not str(params.get("url") or "").strip():
            raise ValidationFailure(
                "parameter", ErrorCode.INVALID_PARAM,
                f"action={action} 必须提供 url",
            )

    if capability in HIGH_RISK_CAPABILITIES:
        _validate_permission_context(capability)


def _validate_permission_context(capability: str) -> None:
    """对已声明租户的高风险 Tool 强制要求真实操作者。

    legacy 本地直调没有可信 tenant 时保持兼容观察模式；一旦入口声明了
    tenant，就不能再用空 actor 执行副作用。
    """
    from backend.tools.session import get_tool_tenant_id, get_tool_user_id

    tenant_id = get_tool_tenant_id()
    actor_id = get_tool_user_id()
    if tenant_id and not actor_id:
        raise ValidationFailure(
            "permission", ErrorCode.PERMISSION_DENIED,
            f"{capability} 缺少可信操作者上下文",
        )


def validate_output(capability: str, output: Any, declared_type: str) -> None:
    """执行输出结构层校验。"""
    if output is None:
        raise ValidationFailure(
            "output", ErrorCode.INTERNAL_ERROR,
            f"{capability} 返回 None",
        )
    # 既有 structured Skill 的兼容契约允许非 JSON 文本原样保留；高风险
    # 能力才把结构不符视为阻断，避免改写成功响应语义。
    if (declared_type == "structured" and not isinstance(output, dict)
            and capability in HIGH_RISK_CAPABILITIES):
        raise ValidationFailure(
            "output", ErrorCode.INTERNAL_ERROR,
            f"{capability} 应返回 object，实际为 {type(output).__name__}",
        )
    required = _STRUCTURED_REQUIRED_FIELDS.get(capability)
    if declared_type == "structured" and isinstance(output, dict) and required:
        missing = sorted(required.difference(output))
        if missing:
            raise ValidationFailure(
                "output", ErrorCode.INTERNAL_ERROR,
                f"{capability} 缺少输出字段: {', '.join(missing)}",
            )
    if declared_type != "structured" and not isinstance(output, str):
        raise ValidationFailure(
            "output", ErrorCode.INTERNAL_ERROR,
            f"{capability} 应返回 text，实际为 {type(output).__name__}",
        )
    if isinstance(output, str) and not output.strip():
        raise ValidationFailure("output", ErrorCode.INTERNAL_ERROR, f"{capability} 返回空文本")


def validate_semantics(capability: str, params: dict, output: Any) -> None:
    """执行业务语义层校验，阻止失败结果继续流入 Reporter。"""
    structured = output
    if isinstance(output, str):
        try:
            parsed = json.loads(output)
            if isinstance(parsed, dict):
                structured = parsed
        except (TypeError, ValueError):
            pass

    # Tool 统一错误协议允许以 JSON 字符串或 dict 形式返回；两种形式都
    # 必须在 Skill 边界被识别，不能被误报成成功并继续交给 Reporter。
    if isinstance(structured, dict) and structured.get("error"):
        detail = str(structured["error"])
        code = (
            ErrorCode.NOT_FOUND
            if any(word in detail for word in ("未找到", "不存在", "查不到"))
            else ErrorCode.UPSTREAM_UNAVAILABLE
        )
        raise ValidationFailure(
            "semantic", code,
            f"{capability} 返回工具错误: {detail[:120]}",
        )

    if not isinstance(output, str):
        return

    stripped = output.strip()
    if stripped.startswith(_FAILURE_PREFIXES):
        raise ValidationFailure(
            "semantic", ErrorCode.UPSTREAM_UNAVAILABLE,
            f"{capability} 返回失败标记",
        )

    if "需要人工审批后执行" in stripped:
        raise ValidationFailure(
            "permission", ErrorCode.PERMISSION_DENIED,
            f"{capability} 尚未完成人工审批",
        )

    if capability.startswith("competitor."):
        action = params.get("action")
        if action in _WRITE_ACTIONS and "请提供" in stripped:
            raise ValidationFailure(
                "semantic", ErrorCode.INVALID_PARAM,
                f"{capability} 的写操作未形成有效目标",
            )


__all__ = [
    "HIGH_RISK_CAPABILITIES",
    "CAPABILITY_VALIDATORS",
    "ValidationFailure",
    "validate_capability_parameters",
    "validate_invocation",
    "validate_output",
    "validate_semantics",
]
