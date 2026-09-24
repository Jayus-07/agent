"""STOP K 路由与域图测试（K6：prefilter 优先级 / 意图判定 / 域图 E2E-离线）。
"""
from __future__ import annotations

import pytest

from backend.orchestration.graph.commerce_prefilter import (
    is_commerce_request,
    try_commerce_prefilter,
)
from backend.travel.commerce.extract import (
    detect_commerce_intent,
    extract_flight_params,
    extract_hotel_params,
)
from backend.travel.commerce.graph_builder import get_commerce_graph
from backend.travel.commerce.graph_state import new_commerce_graph_input


# =============================================
# 意图判定（prefilter 与 slot_filler 单一事实源）
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


# =============================================
# prefilter（开关 + 优先级）
# =============================================


def test_prefilter_off_returns_none(off_mode):
    assert try_commerce_prefilter("帮我找大阪的酒店", {}) is None


def test_prefilter_on_routes(fake_mode):
    update = try_commerce_prefilter("帮我找大阪10月3日到5日的酒店", {})
    assert update is not None
    assert update["route_mode"] == "travel_commerce"
    assert update["route_decision"] is None


def test_prefilter_yields_to_trip_signals(fake_mode):
    """行程信号在场 = 旅游域诉求，commerce 不抢（K0 §1 优先级）。"""
    assert try_commerce_prefilter("大阪5天行程，帮我找酒店", {}) is None
    assert try_commerce_prefilter("福州酒店推荐", {}) is None


def test_prefilter_intent_single_source():
    """prefilter 与 extract 用同一函数（两处判定必然一致）。"""
    from backend.travel.commerce.extract import detect_commerce_intent

    assert is_commerce_request is not None
    assert detect_commerce_intent("帮我找酒店的发票") is None  # CS 让路


# =============================================
# 域图（离线全链：缺槽澄清 → 完整查询）
# =============================================


def _graph():
    return get_commerce_graph()


def test_graph_missing_slots_asks_clarification(fake_mode):
    result = _graph().invoke(new_commerce_graph_input(
        user_message="帮我找大阪的酒店"))
    assert "check_in" in (result.get("commerce_missing") or [])
    assert "入住日期" in result["final_answer"]
    assert result["commerce_status"] == "clarify"


def test_graph_hotel_end_to_end(fake_mode):
    result = _graph().invoke(new_commerce_graph_input(
        user_message="帮我找大阪10月3日到5日的酒店"))
    assert result["commerce_status"] == "success"
    assert result["commerce_offer_count"] >= 1
    assert "酒店搜索结果" in result["final_answer"]
    assert "大阪" in result["final_answer"]


def test_graph_flight_end_to_end(fake_mode):
    result = _graph().invoke(new_commerce_graph_input(
        user_message="帮我查10月3日东京到大阪的机票"))
    assert result["commerce_status"] == "success"
    assert "航班搜索结果" in result["final_answer"]
    assert "东京" in result["final_answer"] or "HND" in result["final_answer"]


def test_graph_unknown_intent_guard(fake_mode):
    """图内兜底：非商务消息进图 = 如实说明（不猜意图不误路由）。"""
    result = _graph().invoke(new_commerce_graph_input(user_message="你好"))
    assert result["commerce_status"] == "clarify"


def test_graph_no_itinerary_state_pollution(fake_mode):
    """G1 边界：commerce 图的 state 不含任何行程域字段。"""
    result = _graph().invoke(new_commerce_graph_input(
        user_message="帮我找大阪10月3日到5日的酒店"))
    for forbidden in ("itinerary", "brief", "candidates", "day_plan",
                      "validation", "travel_context"):
        assert forbidden not in result
