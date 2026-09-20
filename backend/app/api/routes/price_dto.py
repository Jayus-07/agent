"""模型价格治理接口的纯校验与规范化 DTO。"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


PRICE_COMPONENTS = frozenset({"llm", "embedding", "rerank"})
PRICE_DIMENSIONS = frozenset({
    "input", "output", "cache_read", "cache_write", "reasoning", "tool_call",
})
PRICE_UNITS = frozenset({"per_1m_tokens", "per_call"})
REQUIRED_DIMENSIONS = {
    "llm": frozenset({"input", "output"}),
    "embedding": frozenset({"input"}),
    "rerank": frozenset({"input"}),
}
_SIX_PLACES = Decimal("0.000001")


def validate_price_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """校验导入价格行，返回前端可直接展示的错误列表和规范化行。"""
    errors: list[dict[str, Any]] = []
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for index, raw in enumerate(rows):
        row = dict(raw)
        model_name = str(row.get("model_name") or "").strip()
        component = str(row.get("component") or "").strip()
        dimension = str(row.get("dimension") or "").strip()
        unit = str(row.get("unit") or "per_1m_tokens").strip()
        currency = str(row.get("currency") or "USD").strip().upper()
        key = (model_name, component, dimension)

        def add_error(code: str, message: str) -> None:
            errors.append({"row": index, "code": code, "message": message})

        if not model_name:
            add_error("MODEL_REQUIRED", "模型名不能为空")
        if component not in PRICE_COMPONENTS:
            add_error("COMPONENT_INVALID", "组件必须是 llm、embedding 或 rerank")
        if dimension not in PRICE_DIMENSIONS:
            add_error("DIMENSION_INVALID", "价格维度不合法")
        if currency != "USD":
            add_error("CURRENCY_UNSUPPORTED", "价格治理仅接受 USD")
        if unit not in PRICE_UNITS:
            add_error("UNIT_INVALID", "单位必须是 per_1m_tokens 或 per_call")
        if dimension == "tool_call" and unit != "per_call":
            add_error("UNIT_MISMATCH", "tool_call 必须使用 per_call")
        if dimension != "tool_call" and unit == "per_call":
            add_error("UNIT_MISMATCH", "Token 维度必须使用 per_1m_tokens")
        if key in seen:
            add_error("DUPLICATE_ROW", "同一模型、组件、维度只能出现一次")
        seen.add(key)

        try:
            price = Decimal(str(row.get("price_per_unit")))
            if price < 0:
                add_error("PRICE_NEGATIVE", "价格不能为负数")
            price = price.quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)
        except (InvalidOperation, TypeError, ValueError):
            price = Decimal("0")
            add_error("PRICE_INVALID", "价格必须是非负数字")

        if not any(item["row"] == index for item in errors):
            normalized.append({
                "model_name": model_name,
                "component": component,
                "dimension": dimension,
                "price_per_unit": format(price, "f"),
                "unit": unit,
                "currency": "USD",
            })

    return {"valid": not errors and bool(rows), "errors": errors, "rows": normalized}


def parse_price_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """校验并返回数据库写入格式；非法输入由调用方转换为 422。"""
    result = validate_price_rows(rows)
    if not result["valid"]:
        raise ValueError(result["errors"] or "价格行不能为空")
    return result["rows"]


def coverage_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """按模型/组件检查必需维度，供 24 小时灰度完成门使用。"""
    groups: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        key = (str(row.get("model_name") or ""), str(row.get("component") or ""))
        groups.setdefault(key, set()).add(str(row.get("dimension") or ""))
    missing: list[dict[str, str]] = []
    expected = 0
    covered = 0
    for (model_name, component), dimensions in sorted(groups.items()):
        required = REQUIRED_DIMENSIONS.get(component, frozenset())
        expected += len(required)
        covered += len(required & dimensions)
        for dimension in sorted(required - dimensions):
            missing.append({
                "model_name": model_name,
                "component": component,
                "dimension": dimension,
            })
    ratio = (covered / expected) if expected else 0.0
    return {
        "coverage_ratio": ratio,
        "missing_price_count": len(missing),
        "price_calculation_error_count": 0,
        "missing": missing,
    }


__all__ = ["coverage_report", "parse_price_rows", "validate_price_rows"]
