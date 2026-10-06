"""不依赖第三方运行时的 JSON Schema 子集校验器。

支持 ToolSpec 需要的 type/required/enum/additionalProperties、字符串/数值
边界、pattern、数组约束、嵌套 object、items 与 nullable。对象 Schema 未
明确声明 additionalProperties 时默认拒绝未知字段。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SchemaIssue:
    path: str
    message: str


class SchemaValidationError(ValueError):
    """JSON Schema 校验失败。"""

    def __init__(self, issues: list[SchemaIssue]):
        self.issues = issues
        summary = "; ".join(f"{i.path}: {i.message}" for i in issues)
        super().__init__(summary)


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


def _equal(left: Any, right: Any) -> bool:
    try:
        return json.dumps(left, ensure_ascii=False, sort_keys=True) == json.dumps(
            right, ensure_ascii=False, sort_keys=True
        )
    except (TypeError, ValueError):
        return left == right


def _check(value: Any, schema: dict[str, Any], path: str, issues: list[SchemaIssue]) -> None:
    if value is None and schema.get("nullable") is True:
        return

    expected = schema.get("type")
    expected_types = expected if isinstance(expected, list) else [expected]
    expected_types = [item for item in expected_types if item]
    if expected_types and not any(_type_matches(value, item) for item in expected_types):
        issues.append(SchemaIssue(path, f"类型应为 {expected_types}，实际为 {type(value).__name__}"))
        return

    if "enum" in schema and not any(_equal(value, item) for item in schema["enum"]):
        issues.append(SchemaIssue(path, f"取值不在允许范围 {schema['enum']}"))

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            issues.append(SchemaIssue(path, f"长度不能小于 {schema['minLength']}"))
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            issues.append(SchemaIssue(path, f"长度不能超过 {schema['maxLength']}"))
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], value) is not None
            except re.error as exc:
                issues.append(SchemaIssue(path, f"pattern 非法: {exc}"))
                matched = True
            if not matched:
                issues.append(SchemaIssue(path, "不符合 pattern"))

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            issues.append(SchemaIssue(path, f"不能小于 {schema['minimum']}"))
        if "maximum" in schema and value > schema["maximum"]:
            issues.append(SchemaIssue(path, f"不能大于 {schema['maximum']}"))
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            issues.append(SchemaIssue(path, f"必须大于 {schema['exclusiveMinimum']}"))
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            issues.append(SchemaIssue(path, f"必须小于 {schema['exclusiveMaximum']}"))

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            issues.append(SchemaIssue(path, f"元素数量不能少于 {schema['minItems']}"))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            issues.append(SchemaIssue(path, f"元素数量不能超过 {schema['maxItems']}"))
        if schema.get("uniqueItems"):
            seen: list[Any] = []
            for item in value:
                if any(_equal(item, previous) for previous in seen):
                    issues.append(SchemaIssue(path, "数组元素不能重复"))
                    break
                seen.append(item)
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _check(item, item_schema, f"{path}[{index}]", issues)

    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        for name in required:
            if name not in value or value[name] is None:
                issues.append(SchemaIssue(path, f"缺少必填字段 {name}"))
        for name, item in value.items():
            if name in properties:
                _check(item, properties[name], f"{path}.{name}", issues)
                continue
            additional = schema.get("additionalProperties", False)
            if additional is False:
                issues.append(SchemaIssue(path, f"不允许未声明参数 {name}"))
            elif isinstance(additional, dict):
                _check(item, additional, f"{path}.{name}", issues)


def validate_json_schema(value: Any, schema: dict[str, Any]) -> None:
    """校验 value，不通过时抛出带路径的 SchemaValidationError。"""

    if not isinstance(schema, dict):
        raise SchemaValidationError([SchemaIssue("$", "Schema 必须是 object")])
    issues: list[SchemaIssue] = []
    _check(value, schema, "$", issues)
    if issues:
        raise SchemaValidationError(issues)


def legacy_params_to_schema(
    params_schema: dict[str, Any], *, include_auto: bool = False
) -> dict[str, Any]:
    """把现有 capabilities.yaml 的字段式声明转换为 JSON Schema。"""

    if "properties" in params_schema and "type" in params_schema:
        return params_schema
    properties: dict[str, dict[str, Any]] = {}
    required: list[str] = []
    for name, raw in params_schema.items():
        if isinstance(raw, str):
            spec = {"type": "string", "description": raw}
        elif isinstance(raw, dict):
            spec = dict(raw)
        else:
            spec = {"type": "string"}
        is_auto = bool(spec.pop("auto", False))
        if is_auto and not include_auto:
            continue
        spec["type"] = "integer" if spec.get("type") == "int" else spec.get("type", "string")
        spec.pop("required", None)
        # 现有 manifest 中 ``type: object`` 且没有 properties 的字段（如
        # previous_outputs/sql_result、filters）表示业务对象，不是一个空
        # 对象。根参数仍默认 additionalProperties=false，嵌套业务对象允许
        # 其自身字段由下游契约继续解释。
        if spec["type"] == "object" and "properties" not in spec:
            spec["additionalProperties"] = True
        properties[name] = spec
        if raw.get("required") if isinstance(raw, dict) else False:
            if include_auto or not is_auto:
                required.append(name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


__all__ = [
    "SchemaIssue",
    "SchemaValidationError",
    "legacy_params_to_schema",
    "validate_json_schema",
]
