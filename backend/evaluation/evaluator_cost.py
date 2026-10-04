"""评测器成本折算（C4-9/RAGAS-14/COST-08）。

evaluator token（judge/RAGAS 的 LLM 调用）× 模型单价 → 估算成本（CNY 本位币）。
无价格配置 → ``unavailable``（None），**不显示 ¥0**（COST-09 口径延续：
算不出就说算不出，0 元与免费是两回事）。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

FX_UNKNOWN = None


def estimate_evaluator_cost_cny(
    token_summary: dict[str, Any] | None,
    *,
    model_name: str = "",
) -> dict[str, Any] | None:
    """从 token_summary.evaluator 侧 token 估算成本。

    Returns:
        {"model", "tokens", "cost_cny", "basis"} 或 None（无 token 记录）；
        cost_cny=None 表示价格不可得（unavailable 语义，调用方不得渲染 ¥0）。
    """
    if not isinstance(token_summary, dict):
        return None
    evaluator = token_summary.get("evaluator") or {}
    tokens = int(evaluator.get("judge", 0) or 0)
    if tokens <= 0:
        return None

    model = model_name or _eval_gen_model_name()
    price = _model_price(model)
    if price is None:
        return {
            "model": model,
            "tokens": tokens,
            "cost_cny": None,
            "basis": "unavailable_price",
        }
    input_per_1m, output_per_1m = price
    # evaluator 侧只有总 token 读数（无 input/output 拆分），按 input 价
    # 估算下界并在 basis 注明口径——诚实标注而不是假装精确
    cost_usd = Decimal(str(tokens)) * Decimal(str(input_per_1m)) / Decimal("1000000")
    cost_cny = _to_cny(cost_usd)
    return {
        "model": model,
        "tokens": tokens,
        "cost_cny": float(cost_cny) if cost_cny is not None else None,
        "basis": "input_price_estimate_total_tokens",
        "input_price_per_1m_usd": input_per_1m,
    }


def _eval_gen_model_name() -> str:
    try:
        from backend.evaluation.generation import resolve_eval_model

        model, _provider = resolve_eval_model()
        return model
    except Exception:
        return ""


def _model_price(model: str) -> tuple[float, float] | None:
    """(input, output) USD/1M；价格未知返回 None（不可得 ≠ 0 元）。"""
    if not model:
        return None
    try:
        from backend.infra.llm.models import get_model_pricing

        input_price, output_price = get_model_pricing(model)
    except Exception:
        return None
    if input_price <= 0 and output_price <= 0:
        return None
    return input_price, output_price


def _to_cny(cost_usd: Decimal) -> float | None:
    try:
        from backend.config.budget import BUDGET_FX_USD_CNY_RAW

        fx = Decimal(BUDGET_FX_USD_CNY_RAW)
        return float((cost_usd * fx).quantize(Decimal("0.0001")))
    except Exception:
        return None


__all__ = ["estimate_evaluator_cost_cny"]
