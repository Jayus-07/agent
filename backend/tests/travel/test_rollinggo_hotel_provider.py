# -*- coding: utf-8 -*-
"""rollinggo MCP live 酒店适配器契约测试（travel-v2-production P1）。

- 真实样本回放：fixture 为 2026-11-01 经管理端授权链路录制的 searchHotels
  真实返回（脱敏说明见 fixture _meta；酒店名称/价格/地址/坐标为公开商业
  事实，同 AMAP 样本先例）。
- live 冒烟（真实出网）由 TRAVEL_ROLLINGGO_LIVE_SMOKE=1 显式门控，与
  AMAP live 冒烟同模式；CI 离线回放不触网。
- fail-closed：运行时不在场 = disabled；业务封套失败/形状不符 = 显式信号；
  多间房/携儿童通道无法如实表达 = 拒绝，不静默降级。
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.providers.travel.live import cache as pcache
from backend.providers.travel.live.commerce_adapter_base import (
    CommerceSearchSignal,
)
from backend.providers.travel.live.rollinggo_mcp import (
    RollinggoHotelSearchProvider,
    _mcp_args,
    _records_from_payload,
    resolve_live_hotel_provider,
)
from backend.travel.providers_support.commerce import offer_search

_FIXTURE = Path(__file__).parent / "fixtures" / "rollinggo_searchhotels_sample.json"


def _payload() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))["payload"]


def _future(nights: int = 2):
    ci = date.today() + timedelta(days=30)
    return ci, ci + timedelta(days=nights)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """隔离 .env 与共享缓存（与收编件测试基座同口径）。"""
    from backend.infra.cache.backend import InMemoryCache
    from backend.config import travel_offer_search as cfg

    store = InMemoryCache(default_ttl=3600)
    monkeypatch.setattr(pcache, "_backend", lambda: store)
    monkeypatch.setattr(cfg, "TRAVEL_COMMERCE_PROVIDER_MODE", "live")
    monkeypatch.setattr(
        cfg, "TRAVEL_COMMERCE_DEEPLINK_ALLOWED_HOSTS", ("rollinggo.cn",))
    monkeypatch.setattr(cfg, "TRAVEL_COMMERCE_MAX_OFFERS", 10)


# =============================================
# 响应 → Record 映射（真实样本回放）
# =============================================


def test_fixture_payload_maps_to_records():
    ci, co = _future()
    records = _records_from_payload(
        _payload(), city="上海", check_in=ci.isoformat(),
        check_out=co.isoformat(), adults=2, children=0, rooms=1, nights=2)
    assert len(records) == 2
    first = records[0]
    assert first.provider == "rollinggo:mcp"
    assert first.property_id == "29527"
    assert first.property_name == "上海裕景大饭店"
    # message 显式总价优先：992（2 晚总价），非每晚价 496
    assert first.price.amount == "992"
    assert first.price.currency == "CNY"
    assert first.availability == "unknown"      # 搜索响应无房态，不推断
    assert first.lat == pytest.approx(31.240959)
    assert first.booking_deep_link and "rollinggo.cn" in first.booking_deep_link
    assert first.extra.get("nightly_price") == "496.0"


def test_total_fallback_nightly_times_nights(monkeypatch):
    """message 无显式总价时兜底 = 每晚价 × 晚数（确定性算术组合）。"""
    payload = _payload()
    for item in payload["hotelInformationList"]:
        item["price"]["message"] = "查价成功"
    ci, co = _future()
    records = _records_from_payload(
        payload, city="上海", check_in=ci.isoformat(),
        check_out=co.isoformat(), adults=2, children=0, rooms=1, nights=2)
    assert records[0].price.amount == "992"    # 496 × 2
    assert records[1].price.amount == "1694"   # 847 × 2


def test_unpriced_hotels_filtered_and_all_unpriced_is_empty():
    payload = _payload()
    for item in payload["hotelInformationList"]:
        item["price"]["hasPrice"] = False
    ci, co = _future()
    records = _records_from_payload(
        payload, city="上海", check_in=ci.isoformat(),
        check_out=co.isoformat(), adults=2, children=0, rooms=1, nights=2)
    assert records == []  # 全部未报价 = 无可报价候选（empty），非 invalid_response


def test_business_failure_and_bad_shape_are_signals():
    with pytest.raises(CommerceSearchSignal):
        _records_from_payload(
            {"success": False, "code": 500, "message": "boom"},
            city="上海", check_in="2026-12-01", check_out="2026-12-03",
            adults=2, children=0, rooms=1, nights=2)
    with pytest.raises(CommerceSearchSignal):
        _records_from_payload(
            {"success": True, "code": 2000},  # 缺 hotelInformationList
            city="上海", check_in="2026-12-01", check_out="2026-12-03",
            adults=2, children=0, rooms=1, nights=2)


def test_channel_limits_rejected_not_clamped():
    """多间房/携儿童无法如实表达 → 显式拒绝（不静默降级为假答案）。"""
    with pytest.raises(CommerceSearchSignal):
        _mcp_args("上海", nights=2, adults=2, children=0, rooms=2,
                  star_rating=None)
    with pytest.raises(CommerceSearchSignal):
        _mcp_args("上海", nights=2, adults=2, children=1, rooms=1,
                  star_rating=None)
    args = _mcp_args("上海", nights=2, adults=2, children=0, rooms=1,
                     star_rating=4)
    assert args["place"] == "上海" and args["placeType"] == "城市"
    assert args["checkInParam"]["stayNights"] == 2
    assert args["filterOptions"]["starRatings"] == [4.0, 5.0]


# =============================================
# 三态 factory：live 解析与 fail-closed
# =============================================


def test_live_without_runtime_is_disabled_not_fake(monkeypatch):
    from backend.infra.mcp_registry import store as mcp_store

    mcp_store.reset_for_tests()  # 防其他用例泄漏的注入快照干扰「不在场」前提
    monkeypatch.setattr(
        "backend.config.travel_offer_search."
        "TRAVEL_ROLLINGGO_HOTEL_CAPABILITY", "mcp.rollinggo__searchhotels")
    provider, reason = resolve_live_hotel_provider()
    assert provider is None
    assert "未在 MCP 运行时生效" in reason
    # 不回退 fake：resolve 结果不可能是 FakeHotelSearchProvider
    from backend.providers.travel.live.fake_commerce import (
        FakeHotelSearchProvider,
    )
    assert not isinstance(provider, FakeHotelSearchProvider)


def test_service_live_end_to_end_with_injected_runtime(monkeypatch):
    """live 全链离线回放：注入运行时快照 + fixture 响应 → success 真实报价。"""
    from backend.config import travel_offer_search as cfg
    from backend.infra.mcp_registry import specs as spec_lib
    from backend.infra.mcp_registry import store as mcp_store
    from backend.travel_v2.services.hotel_search_service import (
        search_hotel_offers,
    )

    monkeypatch.setattr(
        cfg, "TRAVEL_ROLLINGGO_HOTEL_CAPABILITY",
        "mcp.rollinggo__searchhotels")
    governance = {"operation": "read", "risk_level": "R1",
                  "confirmation_policy": "none"}
    runtime = mcp_store.McpToolRuntime(
        capability="mcp.rollinggo__searchhotels", tool_id=545,
        connection_id="conn_rollinggo",
        upstream_tool_name="searchHotels", tenant_id="default",
        spec=spec_lib.derive_tool_spec(
            "mcp.rollinggo__searchhotels", "酒店搜索",
            {"type": "object"}, governance),
        network_scope="public")
    mcp_store.inject_snapshot_for_tests(mcp_store.McpRegistrySnapshot(
        loaded=True,
        tools={"mcp.rollinggo__searchhotels": runtime},
        connections={"conn_rollinggo": mcp_store.McpConnectionRuntime(
            connection_id="conn_rollinggo",
            base_url="https://mcp.rollinggo.cn/mcp",
            auth_type="static_bearer", network_scope="public")},
    ))
    try:
        monkeypatch.setattr(
            "backend.providers.travel.live.rollinggo_mcp._invoke_mcp",
            lambda capability, params: _payload())
        ci, co = _future()
        out = search_hotel_offers(city="上海", check_in=ci, check_out=co)
    finally:
        mcp_store.reset_for_tests()

    assert out["status"] == "success"
    assert out["source"] == "rollinggo:mcp"
    assert len(out["offers"]) == 2
    offer = out["offers"][0]
    # selection_id = offer_fingerprint（预订 Quote 定位键；重搜确定性）
    assert offer["selection_id"]
    assert offer["property_name"] == "上海裕景大饭店"
    assert offer["price_total"] == "992"       # 总价契约（非每晚 496）
    assert offer["currency"] == "CNY"
    assert offer["booking_deep_link"] and "rollinggo.cn" in offer["booking_deep_link"]
    assert "随时可能变化" in out["disclosure"]  # live 披露（非「测试数据」）


def test_service_live_mcp_failure_stays_fail_closed(monkeypatch):
    """MCP 通道失败 → unavailable（如实），绝不回退 fake 冒充（§29）。"""
    from backend.config import travel_offer_search as cfg
    from backend.infra.mcp_registry import specs as spec_lib
    from backend.infra.mcp_registry import store as mcp_store
    from backend.travel_v2.services.hotel_search_service import (
        search_hotel_offers,
    )

    monkeypatch.setattr(
        cfg, "TRAVEL_ROLLINGGO_HOTEL_CAPABILITY",
        "mcp.rollinggo__searchhotels")
    runtime = mcp_store.McpToolRuntime(
        capability="mcp.rollinggo__searchhotels", tool_id=545,
        connection_id="conn_rollinggo",
        upstream_tool_name="searchHotels", tenant_id="default",
        spec=spec_lib.derive_tool_spec(
            "mcp.rollinggo__searchhotels", "酒店搜索",
            {"type": "object"},
            {"operation": "read", "risk_level": "R1",
             "confirmation_policy": "none"}),
        network_scope="public")
    mcp_store.inject_snapshot_for_tests(mcp_store.McpRegistrySnapshot(
        loaded=True,
        tools={"mcp.rollinggo__searchhotels": runtime},
        connections={"conn_rollinggo": mcp_store.McpConnectionRuntime(
            connection_id="conn_rollinggo",
            base_url="https://mcp.rollinggo.cn/mcp",
            auth_type="static_bearer", network_scope="public")},
    ))
    try:
        def _boom(capability, params):
            raise CommerceSearchSignal(
                offer_search.ProviderStatus.UNAVAILABLE, "连接失败")

        monkeypatch.setattr(
            "backend.providers.travel.live.rollinggo_mcp._invoke_mcp", _boom)
        ci, co = _future()
        out = search_hotel_offers(city="上海", check_in=ci, check_out=co)
    finally:
        mcp_store.reset_for_tests()

    assert out["status"] == "unavailable"
    assert out["offers"] == []


# =============================================
# live 冒烟（真实出网；env 门控，CI 不跑）
# =============================================


@pytest.mark.skipif(
    not __import__("os").environ.get("TRAVEL_ROLLINGGO_LIVE_SMOKE"),
    reason="TRAVEL_ROLLINGGO_LIVE_SMOKE 未开启（真实出网冒烟）")
def test_live_smoke_real_invocation(monkeypatch):
    """真实出网冒烟：需要已生效的 rollinggo 连接与共享栈快照。

    复跑（本机）：
      TRAVEL_ROLLINGGO_LIVE_SMOKE=1 NO_PROXY=mcp.rollinggo.cn \\
        pytest backend/tests/travel/test_rollinggo_hotel_provider.py::test_live_smoke_real_invocation
    """
    from backend.config import travel_offer_search as cfg
    from backend.infra.mcp_registry import store as mcp_store
    from backend.infra.async_utils import run_async
    from backend.travel_v2.services.hotel_search_service import (
        search_hotel_offers,
    )

    # 本进程快照从 DB 拉一次（与 app worker 的 15s 轮询同源；需 PG 5433 可达）
    assert run_async(mcp_store.refresh()), "快照加载失败（DB 不可达或无生效工具）"
    monkeypatch.setattr(
        cfg, "TRAVEL_COMMERCE_DEEPLINK_ALLOWED_HOSTS", ("rollinggo.cn",))
    ci, co = _future()
    out = search_hotel_offers(city="上海", check_in=ci, check_out=co)
    if out["status"] == "disabled":
        pytest.fail("live 冒烟要求 rollinggo 连接在运行时生效（当前 disabled）")
    assert out["status"] == "success", out["reason"]
    assert out["offers"], "live 冒烟期望真实报价候选"
    assert out["source"] == "rollinggo:mcp"
    offer = out["offers"][0]
    assert offer["provider"] == "rollinggo:mcp"
    assert float(offer["price_total"]) > 0
