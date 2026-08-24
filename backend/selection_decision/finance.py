"""selection_decision/finance.py — 财务测算规则计算（用户填参数+规则计算）

设计：
- compute_model：纯函数，参数 → 利润模型（单件利润/利润率/盈亏平衡/风险缓冲金）
- run_finance：有界优化循环（≤3 轮）——不达标时按固定策略调价(+5%)/降本(-5%)重算，
  超限输出 fail + 差距分析。数字全部来自入参与公式，全程可溯源（事实锁定）。
"""
from __future__ import annotations

import math
from typing import Any

from backend.shared.logger import logger

MAX_ROUNDS = 3
# 每轮优化调整幅度：提价 5% + 降本 5%（规则建议，不自动执行）
PRICE_STEP = 0.05
COST_STEP = 0.05

DEFAULTS = {
    "platform_fee_rate": 0.05,
    "shipping_cost": 0.0,
    "marketing_cost": 0.0,
    "monthly_fixed_cost": 0.0,
    "min_margin_rate": 0.25,
    "initial_inventory": 100,
    "buffer_rate": 0.15,
}


def _validate(params: dict[str, Any]) -> None:
    if params.get("sell_price", 0) <= 0:
        raise ValueError("sell_price 必须 > 0")
    if params.get("unit_cost", 0) < 0:
        raise ValueError("unit_cost 不能为负")
    if not 0 <= params.get("platform_fee_rate", 0) < 1:
        raise ValueError("platform_fee_rate 必须在 [0, 1) 区间")
    if params.get("initial_inventory", 0) <= 0:
        raise ValueError("initial_inventory 必须 > 0")
    for field in ("shipping_cost", "marketing_cost", "monthly_fixed_cost"):
        if params.get(field, 0) < 0:
            raise ValueError(f"{field} 不能为负")
    for field in ("buffer_rate", "min_margin_rate"):
        if not 0 <= params.get(field, 0) <= 1:
            raise ValueError(f"{field} 必须在 [0, 1] 区间")


def compute_model(params: dict[str, Any]) -> dict[str, Any]:
    """单次利润模型计算（纯函数）"""
    merged = {**DEFAULTS, **params}
    _validate(merged)
    sell = merged["sell_price"]
    net_price = sell * (1 - merged["platform_fee_rate"])
    unit_margin = net_price - merged["unit_cost"] \
        - merged["shipping_cost"] - merged["marketing_cost"]
    margin_rate = unit_margin / sell
    break_even = (math.ceil(merged["monthly_fixed_cost"] / unit_margin)
                  if unit_margin > 0 and merged["monthly_fixed_cost"] > 0 else None)
    first_batch = merged["initial_inventory"] * (
        merged["unit_cost"] + merged["shipping_cost"])
    return {
        "sell_price": sell,
        "unit_cost": merged["unit_cost"],
        "net_price": round(net_price, 2),
        "unit_margin": round(unit_margin, 2),
        "margin_rate": margin_rate,
        "break_even_units": break_even,
        "first_batch_investment": round(first_batch, 2),
        "risk_buffer": round(first_batch * merged["buffer_rate"], 2),
    }


def run_finance(params: dict[str, Any], max_rounds: int = MAX_ROUNDS) -> dict[str, Any]:
    """有界优化循环：不达标 → 提价/降本建议 → 重算（≤max_rounds 轮）"""
    max_rounds = max(1, max_rounds)
    current = dict(params)
    rounds: list[dict[str, Any]] = []
    suggestions: list[str] = []
    verdict = "fail"
    for i in range(1, max_rounds + 1):
        model = compute_model(current)
        passed = model["unit_margin"] > 0 and \
            model["margin_rate"] >= current.get("min_margin_rate", DEFAULTS["min_margin_rate"])
        rounds.append({"round": i, "passed": passed, "model": model})
        if passed:
            verdict = "pass"
            break
        # 优化建议（规则固定策略，数字可溯源）
        new_price = round(current["sell_price"] * (1 + PRICE_STEP), 2)
        new_cost = round(current["unit_cost"] * (1 - COST_STEP), 2)
        suggestions.append(
            f"第{i}轮未达标（利润率 {model['margin_rate']:.1%}）：建议提价至 "
            f"{new_price}（+{PRICE_STEP:.0%}）并将采购成本压至 {new_cost}（-{COST_STEP:.0%}）"
        )
        current = {**current, "sell_price": new_price, "unit_cost": new_cost}
    else:
        logger.info("[Finance] 有界优化循环结束仍未达标，输出 fail")
    final = rounds[-1]["model"]
    gap = (current.get("min_margin_rate", DEFAULTS["min_margin_rate"])
           - final["margin_rate"]) if verdict == "fail" else 0.0
    return {
        "verdict": verdict,
        "rounds": rounds,
        "suggestions": suggestions,
        "final_model": final,
        "margin_gap": round(gap, 4),
    }
