"""统一 ToolResult 封套、空数据和输出 Schema 校验。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from backend.core.tool_governance.models import ToolSpec
from backend.core.tool_governance.schema_validator import (
    SchemaValidationError,
    validate_json_schema,
)


@dataclass(frozen=True)
class ToolResultEnvelope:
    status: str
    data: Any = None
    error: dict[str, Any] | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "data": self.data,
            "error": self.error,
            "meta": self.meta,
        }


def _decode(raw: Any) -> Any:
    if not isinstance(raw, str):
        return raw
    stripped = raw.strip()
    if not stripped:
        return ""
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return raw


def _is_empty(data: Any) -> bool:
    if data is None or data == "":
        return True
    if isinstance(data, (list, tuple, set, dict)) and not data:
        return True
    if isinstance(data, dict):
        items = data.get("items")
        if isinstance(items, list) and not items:
            return True
        if data.get("total") == 0:
            return True
    return False


def validate_tool_result(raw: Any, spec: ToolSpec) -> ToolResultEnvelope:
    """把 Provider 原始返回变成唯一封套；异常不允许流入 Reporter。"""

    value = _decode(raw)
    if isinstance(value, dict) and value.get("status") in {"failed", "error"}:
        error = value.get("error")
        error_dict = error if isinstance(error, dict) else {
            "code": "upstream_unavailable",
            "message": str(error or "工具执行失败"),
            "retryable": False,
        }
        return ToolResultEnvelope("failed", data=None, error=error_dict)
    if isinstance(value, dict) and value.get("status") in {"success", "empty", "degraded"}:
        status = str(value["status"])
        data = value.get("data")
        if status == "success" and _is_empty(data):
            status = "empty"
        if spec.output_schema and data is not None:
            try:
                validate_json_schema(data, spec.output_schema)
            except SchemaValidationError as exc:
                return ToolResultEnvelope(
                    "failed",
                    error={"code": "output_invalid", "message": str(exc), "retryable": False},
                )
        return ToolResultEnvelope(status, data=data, meta=value.get("meta") or {})

    if isinstance(value, str) and value.lstrip().lower().startswith(("<html", "<!doctype")):
        return ToolResultEnvelope(
            "failed",
            error={"code": "output_invalid", "message": "上游返回了 HTML 错误页", "retryable": False},
        )
    if _is_empty(value):
        return ToolResultEnvelope("empty", data=value)
    if spec.output_schema:
        try:
            validate_json_schema(value, spec.output_schema)
        except SchemaValidationError as exc:
            return ToolResultEnvelope(
                "failed",
                error={"code": "output_invalid", "message": str(exc), "retryable": False},
            )
    return ToolResultEnvelope("success", data=value)


__all__ = ["ToolResultEnvelope", "validate_tool_result"]
