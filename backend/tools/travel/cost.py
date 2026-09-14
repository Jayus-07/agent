"""tools/travel/cost.py — 费用估算（纯函数）

费用口径（P0 为城市均值占位，全部可在 config/travel.py 调参）：
  门票 = Σ 各到访 POI 票价 × 人数
  餐饮 = 人均日餐费 × 天数 × 人数
  住宿 = 每晚房价 × (天数 - 1) 夜 × 房间数（2 人 1 间）
  通勤 = Σ 各段整车费用（不乘人数，见 routing.leg_cost_cny）

拆分保留而非只给总额：用户问「为什么超了」时必须能答出来，
这也是 validator 的 BUDGET_OVER 能做归因、reporter 能给出省钱建议的前提。
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


def day_cost(day: ItineraryDay, party_size: int) -> float:
    """单日花费 = 门票×人数 + 人均日餐费×人数 + 通勤。

    住宿不摊到单日：末晚不产生住宿，摊到某一天会误导用户以为那天特别贵。
    """
    meals = T.TRAVEL_MEAL_PER_DAY_CNY * max(1, party_size)
    return round(day_visit_tickets(day) * max(1, party_size) + meals
                 + day_transit_cost(day), 2)


def estimate_cost(days: list[ItineraryDay], party_size: int) -> CostBreakdown:
    """汇总全程费用拆分。

    Args:
        days: 全部行程日（用于累加门票与通勤）
        party_size: 同行人数
    """
    people = max(1, party_size)
    night_count = max(0, len(days) - 1)

    return CostBreakdown(
        tickets=round(sum(day_visit_tickets(d) for d in days) * people, 2),
        meals=round(T.TRAVEL_MEAL_PER_DAY_CNY * people * len(days), 2),
        lodging=round(T.TRAVEL_LODGING_PER_NIGHT_CNY * night_count
                      * rooms_needed(people), 2),
        transit=round(sum(day_transit_cost(d) for d in days), 2),
    )
