"""收编件意图判定与参数抽取（迁自 tests/travel/commerce/test_commerce_routing.py
的纯函数段，2026-11-01 收编重构）。

prefilter 与域图壳已删除，detect_commerce_intent / extract_* 作为收编件
被 main 锁域引导（lock_domain）与跨轮续填（booking_pending_resolver）复用
——单一事实源的回归守护随迁。
"""
from __future__ import annotations

import pytest

from backend.travel.providers_support.commerce.extract import (
    detect_commerce_intent,
    extract_flight_params,
    extract_hotel_params,
)


# =============================================
# 意图判定（路由引导与槽位抽取单一事实源）
# =============================================


@pytest.mark.parametrize("query,expected", [
    ("帮我找大阪10月3日到5日的酒店", "hotel"),
    ("帮我找酒店", "hotel"),
    ("大阪的酒店", "hotel"),
    ("东京酒店多少钱", "hotel"),
    ("帮我订一间民宿", "hotel"),
    ("帮我查10月3日东京到大阪的机票", "flight"),
    ("查一下机票", "flight"),
    ("有东京到大阪的航班吗", "flight"),
])
def test_intent_positive(query, expected):
    assert detect_commerce_intent(query) == expected


@pytest.mark.parametrize("query", [
    "大阪5天行程，帮我找酒店",     # 行程信号 → 旅游域
    "大阪旅游攻略",               # 纯规划
    "我的机票订单在哪",           # CS 售后
    "酒店推荐",                   # 旅游信号（住宿推荐类）
    "近3天订单量",                # 电商
    "",
])
def test_intent_negative(query):
    assert detect_commerce_intent(query) is None


# =============================================
# 参数抽取（确定性；缺失槽位不出现）
# =============================================


def test_hotel_param_extraction_full():
    params = extract_hotel_params("帮我找大阪10月3日到5日的酒店，2个人")
    assert params["city"] == "大阪"
    assert params["check_in"].month == 10 and params["check_in"].day == 3
    assert params["check_out"].month == 10 and params["check_out"].day == 5
    assert (params["check_out"] - params["check_in"]).days == 2
    assert params["adults"] == 2
    assert "rooms" not in params  # 未提供 → 不出现（读方 .get()）


def test_hotel_param_extraction_missing_dates():
    params = extract_hotel_params("帮我找大阪的酒店")
    assert params["city"] == "大阪"
    assert "check_in" not in params and "check_out" not in params


def test_flight_param_extraction():
    params = extract_flight_params("帮我查10月3日东京到大阪的机票")
    assert params["origin"] == "东京"
    assert params["destination"] == "大阪"
    assert params["departure_date"].month == 10
    assert params["departure_date"].day == 3
