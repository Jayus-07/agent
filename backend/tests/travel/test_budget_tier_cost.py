"""tests/travel/test_budget_tier_cost.py — 平价档（budget）定额与地板单测
（travel-value-showcase B1，2026-11-04）

硬口径（TASK §6）：budget 档 2 人 2 天地板 ≤ ¥400、3 天 ≤ ¥600，
对任意城市（含未登记回落）成立；定额常量改动 = 有意变更，必须同步改
这里的守卫断言（AGENTS.md 第 8 条连带更新）。全部纯函数层，不触网不连库。
"""
from __future__ import annotations

import pytest

from backend.config import travel as T
from backend.tools.travel.cost import (
    day_cost,
    estimate_budget_floor,
    estimate_cost,
)
from backend.travel.models.itinerary import ItineraryDay

from backend.tests.travel.conftest import make_item

# 地板验收硬口径（TASK 决策点 3 拍板；改定额先改这里 = 有意变更可见）
BUDGET_FLOOR_2P2D_MAX = 400.0
BUDGET_FLOOR_2P3D_MAX = 600.0

# 定额守卫（D0 实测定稿，见 docs/tasks/travel-value-showcase/PROGRESS.md）：
# 改任何一格 → 同步 TASK §5 定稿表 + 缺口卡文案 + 金标期望
BUDGET_MEAL_EXPECTED = 35.0
BUDGET_LODGING_EXPECTED = {
    "成都": 90.0, "西安": 90.0, "杭州": 90.0,
    "福州": 150.0, "厦门": 190.0,
}


def test_budget_tier_constants_guarded():
    """定额常量守卫：数字即验收口径，漂移必须显式过本断言。"""
    assert T.TRAVEL_BUDGET_MEAL_PER_DAY_CNY == BUDGET_MEAL_EXPECTED
    assert T.TRAVEL_BUDGET_LODGING_PER_NIGHT_CNY == 130.0
    assert T.TRAVEL_CITY_BUDGET_COST_TIERS == {
        city: {"meal": BUDGET_MEAL_EXPECTED, "lodging": lodging}
        for city, lodging in BUDGET_LODGING_EXPECTED.items()
    }


@pytest.mark.parametrize("city", [*BUDGET_LODGING_EXPECTED, "北京", ""])
def test_budget_floor_acceptance_2p2d_and_2p3d(city):
    """硬口径：budget 档任意城市 2 人 2 天 ≤ ¥400、3 天 ≤ ¥600。"""
    floor_2d = estimate_budget_floor(days_count=2, party_size=2, city=city, tier="budget")
    floor_3d = estimate_budget_floor(days_count=3, party_size=2, city=city, tier="budget")
    assert floor_2d["total"] <= BUDGET_FLOOR_2P2D_MAX, (city, floor_2d)
    assert floor_3d["total"] <= BUDGET_FLOOR_2P3D_MAX, (city, floor_3d)
    # 地板 = 餐饮 + 住宿硬成本，拆分自洽
    assert floor_2d["total"] == round(floor_2d["meals"] + floor_2d["lodging"], 2)


def test_budget_floor_registered_city_numbers():
    """登记城市精确数字（福州 = 餐 140 + 住 150；厦门 3 天 = 餐 210 + 住 380）。"""
    assert estimate_budget_floor(2, 2, "福州", tier="budget") == {
        "meals": 140.0, "lodging": 150.0, "total": 290.0,
    }
    assert estimate_budget_floor(3, 2, "厦门", tier="budget") == {
        "meals": 210.0, "lodging": 380.0, "total": 590.0,
    }
    # 未登记城市回落平价全局定额（餐 35 / 住 130）
    assert estimate_budget_floor(2, 2, "北京", tier="budget") == {
        "meals": 140.0, "lodging": 130.0, "total": 270.0,
    }


def test_budget_floor_default_tier_is_economy_no_regression():
    """默认档（economy）行为零回归：走城市均值表，与旧口径一致。"""
    assert estimate_budget_floor(2, 2, "杭州") == {
        "meals": 160.0 * 2 * 2, "lodging": 520.0, "total": 160.0 * 2 * 2 + 520.0,
    }
    assert estimate_budget_floor(2, 2, "福州", tier="economy")["total"] == 120.0 * 2 * 2 + 350.0


def test_budget_floor_larger_party_uses_rooms():
    """4 人 1 晚 2 间：住宿按 2 间计（rooms_needed 口径不变，只是走平价表）。"""
    floor = estimate_budget_floor(2, 4, "成都", tier="budget")
    assert floor == {"meals": 35.0 * 4 * 2, "lodging": 90.0 * 2, "total": 280.0 + 180.0}


def test_estimate_cost_budget_tier_uses_low_price_table():
    """budget 档 estimate_cost：平价定额 × 乘数 1，economy 城市均值表不受影响。"""
    days = [ItineraryDay(day_index=i, items=[make_item()]) for i in (1, 2)]
    cost = estimate_cost(days, party_size=2, city="厦门", tier="budget")
    assert cost.meals == 35.0 * 2 * 2
    assert cost.lodging == 190.0 * 1  # 2 天 1 晚 1 间
    # economy 对照（红线 4：城市均值 + economy 乘数 1.0 不变）
    cost_economy = estimate_cost(days, party_size=2, city="厦门")
    assert cost_economy.meals == 130.0 * 2 * 2
    assert cost_economy.lodging == 400.0 * 1


def test_day_cost_budget_tier_meal_only():
    """day_cost 带 tier：budget 档餐饮走平价表；默认/economy 与旧版一致。"""
    day = ItineraryDay(day_index=1, items=[make_item()])
    base = day_cost(day, 2, city="厦门")
    assert day_cost(day, 2, city="厦门", tier="budget") == round(base - 130.0 * 2 + 35.0 * 2, 2)
    assert day_cost(day, 2, city="厦门", tier="economy") == base
    # 未识别档位一律回落均值表（与旧签名 day_cost(day, 2) 行为一致）
    assert day_cost(day, 2, city="厦门", tier="") == base
    # 默认 city=""：回落全局均值表（150/人/天），与旧签名行为一致
    assert day_cost(day, 2) == day_cost(day, 2, tier="") == day_cost(day, 2, city="", tier="")
