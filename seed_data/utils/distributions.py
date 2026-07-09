"""概率分布工具 — 支持幂律分布、季节性权重、加权随机选择。

所有函数接收 random.Random 实例，保证全局 seed 可复现。
"""

import random
from typing import Any


def weighted_choice(rng: random.Random, choices: list[Any], weights: list[float]) -> Any:
    """加权随机选择（使用 cumulative distribution）。"""
    if len(choices) != len(weights):
        raise ValueError(f"choices 和 weights 长度必须一致: {len(choices)} vs {len(weights)}")
    total = sum(weights)
    if total == 0:
        return rng.choice(choices)
    norm = [w / total for w in weights]
    # 累积分布
    cumulative = []
    s = 0.0
    for w in norm:
        s += w
        cumulative.append(s)
    r = rng.random()
    for i, c in enumerate(cumulative):
        if r <= c:
            return choices[i]
    return choices[-1]


def weighted_choice_dict(rng: random.Random, mapping: dict[Any, float]) -> Any:
    """Dict 版加权随机选择。"""
    return weighted_choice(rng, list(mapping.keys()), list(mapping.values()))


def pareto_int(rng: random.Random, min_val: float, max_val: float, alpha: float = 1.5) -> int:
    """Pareto 分布采样（适用于价格、销量等长尾分布）。

    alpha 越小，分布越均匀；alpha 越大，越集中在 min_val 附近。
    """
    # 用逆变换法: X = min_val / U^(1/alpha)
    u = rng.random()
    value = min_val / (u ** (1.0 / alpha))
    return int(min(value, max_val))


def seasonal_weight(month: int, monthly_factors: list[float]) -> float:
    """获取某月的季节性权重因子。

    Args:
        month: 1-12
        monthly_factors: 12 个月的因子列表 [Jan, Feb, ..., Dec]
    """
    return monthly_factors[month - 1]


def weekend_boost(weekday: int, boost: float = 1.25) -> float:
    """周末放大因子。weekday: 0=Monday, 6=Sunday。"""
    return boost if weekday >= 5 else 1.0


def gaussian_noise(rng: random.Random, mean: float = 1.0, sigma: float = 0.1) -> float:
    """高斯噪声因子（用于日常波动）。"""
    return max(0.5, min(1.5, rng.gauss(mean, sigma)))


def daily_order_multiplier(date_tuple: tuple, monthly_factors: list[float],
                           weekend_boost_val: float, rng: random.Random) -> float:
    """综合计算某日的订单量乘数 = 月因子 × 周末因子 × 噪声。

    Args:
        date_tuple: (year, month, day, weekday) — weekday: 0=Monday
    """
    import datetime
    _, month, _, weekday = date_tuple
    month_factor = seasonal_weight(month, monthly_factors)
    weekend_factor = weekend_boost(weekday, weekend_boost_val)
    noise = gaussian_noise(rng)
    return month_factor * weekend_factor * noise
