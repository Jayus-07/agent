"""STOP L 路由/域图测试（G1 冻结面顺序 / prefilter / 域图离线全链）。"""
from __future__ import annotations

import pytest

from backend.orchestration.graph.booking_prefilter import (
    is_booking_request,
    try_booking_prefilter,
)
from backend.travel.booking.graph_builder import (
    detect_booking_action,
    get_booking_graph,
)
from backend.travel.booking.graph_state import new_booking_graph_input


# =============================================
# 子意图判定
# =============================================


@pytest.mark.parametrize("query,expected", [
    ("确认预订", "confirm"),
    ("好的，订吧", "confirm"),
    ("我的预订", "status"),
    ("预订状态怎么样", "status"),
    ("帮我预订大阪10月3日到5日的酒店", "new"),
    ("预订一间东京的酒店", "new"),
])
def test_action_positive(query, expected):
    assert detect_booking_action(query) == expected


# =============================================
# prefilter（优先级加法插入：选品 > 预订 > 商务）
# =============================================


def test_prefilter_off_returns_none(monkeypatch):
    from backend.config import travel_booking as cfg

    monkeypatch.setattr(cfg, "TRAVEL_BOOKING_ENABLED", False)
    assert try_booking_prefilter("帮我预订大阪的酒店", {}) is None


def test_prefilter_on_routes(fake_booking_route):
    update = try_booking_prefilter("帮我预订大阪10月3日到5日的酒店", {})
    assert update is not None
    assert update["route_mode"] == "travel_booking"


def test_prefilter_yields_to_cs_and_trip(fake_booking_route):
    assert try_booking_prefilter("我的机票订单退款", {}) is None   # CS 售后
    assert try_booking_prefilter("大阪行程里帮我订酒店", {}) is None  # 行程信号
    assert try_booking_prefilter("帮我找大阪的酒店", {}) is None    # 商务 search


@pytest.fixture
def fake_booking_route(monkeypatch):
    from backend.config import travel_booking as cfg

    monkeypatch.setattr(cfg, "TRAVEL_BOOKING_ENABLED", True)
    return cfg


# =============================================
# 域图离线全链（PG；booking_env 隔离）
# =============================================


def test_graph_new_booking_flow(booking_env, booking_service, monkeypatch):
    """图内走完整链：新预订 → Quote + 待确认卡片。"""
    monkeypatch.setattr(
        "backend.travel.booking.graph_builder.BookingService",
        lambda: booking_service)
    result = get_booking_graph().invoke(new_booking_graph_input(
        user_message="帮我预订大阪10月3日到5日的酒店",
        user_id="user-A", tenant_id="tenant-A"))
    assert result["booking_status"] == "quoted"
    assert "预订确认" in result["final_answer"]
    assert "确认预订" in result["final_answer"]  # 引导显式确认


def test_graph_confirm_flow_double_click(booking_env, booking_service,
                                         monkeypatch, native_factory):
    """L-E2E-3 的离线版：确认 + 重复确认 → 1 order / 1 provider create。"""
    monkeypatch.setattr(
        "backend.travel.booking.graph_builder.BookingService",
        lambda: booking_service)
    booking_service.create_quote(
        tenant_id="tenant-A", user_id="user-A", commerce_type="hotel",
        search_params={"city": "大阪", "check_in": "2026-10-03",
                       "check_out": "2026-10-05", "adults": 2,
                       "children": 0, "rooms": 1},
        selection={"index": 1})
    r1 = get_booking_graph().invoke(new_booking_graph_input(
        user_message="确认预订", user_id="user-A", tenant_id="tenant-A"))
    r2 = get_booking_graph().invoke(new_booking_graph_input(
        user_message="确认预订", user_id="user-A", tenant_id="tenant-A"))
    assert r1["booking_status"] == "booked"
    assert r2["booking_status"] in ("booked", "already_booked")
    from backend.travel.booking.store import BookingStore
    from backend.tests.travel.booking.conftest import _factory

    with _factory() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM travel.booking_orders")
        assert cur.fetchone()[0] == 1
    _ = BookingStore
