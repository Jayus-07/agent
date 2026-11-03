"""tools/travel/cost.py — 费用估算（纯函数）

费用口径：
  门票 = Σ 各到访 POI 票价 × 人数
  餐饮 = 城市档位日餐费 × 天数 × 人数（未登记城市回落全局定额）
  住宿 = 城市档位每晚房价 × (天数 - 1) 夜 × 房间数（2 人 1 间）
  通勤 = Σ 各段整车费用（不乘人数，见 routing.leg_cost_cny）

拆分保留而非只给总额：用户问「为什么超了」时必须能答出来，
这也是 validator 的 BUDGET_OVER 能做归因、reporter 能给出省钱建议的前提。

城市档位（2026-09-22 P0-3）：餐饮/住宿此前是全局均值，对消费水平明显
不同的城市会系统性偏差。city 参数为可选 —— 不传时行为与旧版完全一致，
调用方（budget expert / repair）显式传入 brief.destination。
"""
from __future__ import annotations

import math

from backend.config import travel as T
from backend.travel.models.itinerary import (
    CostBreakdown,
    ItineraryDay,
    KIND_VISIT,
)


def rooms_needed(party_size: int) -> int:
    """按 2 人 1 间折算所需房间数（至少 1 间）。"""
    return max(1, math.ceil(max(1, party_size) / 2))


def day_visit_tickets(day: ItineraryDay) -> float:
    """单日到访门票合计（单人）。"""
    return sum(
        i.poi.ticket_cny for i in day.items
        if i.kind == KIND_VISIT and i.poi is not None
    )


def day_transit_cost(day: ItineraryDay) -> float:
    """单日通勤费用合计（整车）。"""
    return sum(leg.cost_cny for leg in day.legs)


def _city_tier_data(city: str, tier: str) -> dict[str, float]:
    """餐饮/住宿定额取数单点：budget 档走平价低价表，其余档走城市均值表。

    budget 是独立低价口径（M3-e 第三档）：绝对低价才能验收 2 人 2 天
    ≤ ¥400 的硬数字，城市均值 × 乘数算不出绝对低价，也不复用。
    """
    if tier == "budget":
        return T.budget_city_cost_tier(city)
    return T.city_cost_tier(city)


def _tier_multiplier(tier: str) -> dict[str, float]:
    """档位乘数：budget 档定额即绝对值（乘数恒 1），其余档走 TIER_PROFILES。"""
    if tier == "budget":
        return {"meals": 1.0, "lodging": 1.0}
    return (T.TIER_PROFILES.get(tier) or T.TIER_PROFILES["economy"])["cost_multiplier"]


def day_cost(day: ItineraryDay, party_size: int, city: str = "",
             tier: str = "economy") -> float:
    """单日花费 = 门票×人数 + 人均日餐费×人数 + 通勤。

    住宿不摊到单日：末晚不产生住宿，摊到某一天会误导用户以为那天特别贵。
    tier 仅影响餐饮取数表（budget 平价表 / 其余城市均值表）；不传或默认
    economy 时与旧版行为完全一致（transit_service 旧调用点零改动）。
    """
    meal = _city_tier_data(city, tier)["meal"]
    meals = meal * max(1, party_size)
    return round(day_visit_tickets(day) * max(1, party_size) + meals
                 + day_transit_cost(day), 2)


def estimate_cost(
    days: list[ItineraryDay], party_size: int, city: str = "",
    tier: str = "economy",
) -> CostBreakdown:
    """汇总全程费用拆分。

    Args:
        days: 全部行程日（用于累加门票与通勤）
        party_size: 同行人数
        city: 目的地城市键（决定餐饮/住宿档位；空串回落全局定额）
        tier: 方案档位（M3-e）——budget 走平价低价定额（乘数恒 1），
              economy/comfortable 乘数见 TIER_PROFILES；都只作用于
              餐饮/住宿（门票与通勤按实际行程累加，不乘档位）
    """
    people = max(1, party_size)
    night_count = max(0, len(days) - 1)
    tier_data = _city_tier_data(city, tier)
    mult = _tier_multiplier(tier)

    return CostBreakdown(
        tickets=round(sum(day_visit_tickets(d) for d in days) * people, 2),
        meals=round(tier_data["meal"] * people * len(days) * mult["meals"], 2),
        lodging=round(tier_data["lodging"] * night_count * rooms_needed(people) * mult["lodging"], 2),
        transit=round(sum(day_transit_cost(d) for d in days), 2),
    )


def estimate_budget_floor(days_count: int, party_size: int, city: str = "",
                          tier: str = "economy") -> dict:
    """当前资源的**最低可行预算**（档位硬成本，M3-f 缺口卡数据）。

    口径：档位餐饮 + 档位住宿（budget 走平价低价表，economy 及默认走
    城市均值表），门票/通勤按 0 计（行程未定时无从累加）——即「再怎么省
    也省不掉」的部分。预算低于此值时任何排程都是假行程，走删减协商
    而不是硬排。
    """
    people = max(1, party_size)
    night_count = max(0, max(0, days_count) - 1)
    tier_data = _city_tier_data(city, tier)
    meals = round(tier_data["meal"] * people * max(0, days_count), 2)
    lodging = round(tier_data["lodging"] * night_count * rooms_needed(people), 2)
    total = round(meals + lodging, 2)
    return {"meals": meals, "lodging": lodging, "total": total}
