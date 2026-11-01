# -*- coding: utf-8 -*-
"""收编重构 U1 契约测试：AmapPOIProvider + 链账 poi.search。

形态（§6 矩阵 · POI Curator 行）：
- 样本 fixture 按高德「搜索POI 2.0」归一化输出（infra/lbs/amap.normalize_merchant
  的字段契约）构造——真实样本录制在当前执行环境不可用（httpx 0.28.1 对
  restapi.amap.com TLS EOF，沙箱传输层问题，urllib 正常），已标注
  BLOCKED-IN-ENV；live 冒烟用例由 TRAVEL_AMAP_LIVE_SMOKE=1 显式门控。
- fail-closed：未配置 Key → 空候选（不伪造 live）；检索异常 → 链降级 seed。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.providers.travel.poi import (
    AmapPOIProvider,
    FallbackPOIProvider,
    SeedPOIProvider,
)

# 高德 v5 place/text 归一化输出样本（normalize_merchant 契约字段；公开 POI
# 事实，无敏感字段——与真实返回字段一一对应，见 infra/lbs/amap.py）
_AMAP_RECORD = {
    "id": "B0FFG9Z8O2",
    "name": "鼓山",
    "category": "风景名胜;风景名胜;国家级景点",
    "typecode": "110000",
    "rating": 4.4,
    "avg_cost_cny": None,
    "open_time_today": "08:00-17:30",
    "open_time_week": "08:00-17:30",
    "tel": "",
    "business_area": "",
    "address": "晋安区鼓山镇",
    "province": "福建省",
    "city": "福州市",
    "district": "晋安区",
    "adcode": "350100",
    "lat": 26.052,
    "lng": 119.397,
    "distance_m": None,
}


@pytest.fixture(autouse=True)
def _quarantine_env(monkeypatch):
    """隔离本机 .env：AMAP 配置面一律显式打桩（CI 无 Key 也确定）。"""
    import backend.config.map as m

    monkeypatch.setattr(m, "AMAP_ENABLED", True, raising=False)
    monkeypatch.setattr(m, "AMAP_KEY", "test-key-not-real", raising=False)


def test_provider_maps_amap_record_to_poi_with_honest_annotations():
    provider = AmapPOIProvider()
    with patch("backend.infra.lbs.amap.place_text",
               return_value=[dict(_AMAP_RECORD)]):
        pois = provider.search("福州", limit=5)
    assert len(pois) == 1
    poi = pois[0]
    assert poi.name == "鼓山"
    assert poi.source == "amap:live"
    assert poi.verification_status == "unverified"   # 评分/营业时间未核实
    assert poi.location_status == "verified"          # 坐标是权威 LBS 事实
    assert poi.rating == pytest.approx(4.4)
    assert poi.observed_at  # 观测时点必填（时效语义）
    assert any("营业时间" in tag for tag in poi.tags)


def test_provider_unconfigured_returns_empty_not_fake(monkeypatch):
    import backend.config.map as m

    monkeypatch.setattr(m, "AMAP_ENABLED", True, raising=False)
    monkeypatch.setattr(m, "AMAP_KEY", "", raising=False)
    provider = AmapPOIProvider()
    assert provider.is_enabled() is False
    assert provider.search("福州") == []   # fail-closed：不回退 fake 数据


def test_provider_transport_failure_returns_empty():
    """place_text 契约：传输失败/未配置返回 None（不抛）→ Provider 返回空。"""
    provider = AmapPOIProvider()
    with patch("backend.infra.lbs.amap.place_text", return_value=None):
        assert provider.search("福州") == []
        assert provider.resolve("鼓山", "福州") is None


def test_fallback_chain_degrades_to_seed(monkeypatch):
    provider = FallbackPOIProvider(AmapPOIProvider(), SeedPOIProvider())
    assert provider.chain == ("amap:live", "seed:local")
    with patch("backend.infra.lbs.amap.place_text",
               side_effect=RuntimeError("boom")):
        pois = provider.search("福州", limit=3)
    # seed 池（福州在种子城市内）兜底，链路可用
    assert isinstance(pois, list)


def test_chain_ledger_declares_amap_first(monkeypatch):
    from backend.providers.travel.live.router import (
        PROVIDER_CHAINS,
        _is_configured,
        assemble_poi_provider,
    )

    assert PROVIDER_CHAINS["poi.search"] == ("amap_live", "seed")
    assert _is_configured("amap_live") is True
    assembled = assemble_poi_provider()
    assert isinstance(assembled, FallbackPOIProvider)
    assert assembled.chain == ("amap:live", "seed:local")

    import backend.config.map as m

    monkeypatch.setattr(m, "AMAP_KEY", "", raising=False)
    assert _is_configured("amap_live") is False
    assert isinstance(assemble_poi_provider(), SeedPOIProvider)


def test_poi_tool_merges_amap_candidates_with_note():
    import json

    from backend.tools.travel import poi as poi_tool

    class _FakeAmap:
        name = "amap:live"

        def search(self, city, *, preferences=None, limit=20):
            return [poi_tool.Poi(
                poi_id="amap:X1", name="高德候选点", city=city,
                category="景点", lat=26.06, lng=119.3,
                suggested_minutes=120, ticket_cny=0.0, tags=[],
                rating=4.6, source="amap:live",
                observed_at="2026-11-01T00:00:00+00:00",
                verification_status="unverified",
                reason="高德景点类目检索", location_status="verified",
            )]

    class _FakeAssembled:
        chain = ("amap:live", "seed:local")
        _primary = _FakeAmap()

    # _merge_amap_candidates 内做函数级 import（providers.live.router）——
    # 打桩点在来源模块，与实现解耦
    import backend.providers.travel.live.router as chain_router

    with patch.object(chain_router, "assemble_poi_provider",
                      return_value=_FakeAssembled()):
        raw = poi_tool.travel_poi_search_tool.func(city="福州") \
            if hasattr(poi_tool.travel_poi_search_tool, "func") \
            else poi_tool.travel_poi_search_tool(city="福州")
    payload = json.loads(raw)
    assert payload["status"] == "success"
    notes = payload.get("notes") or []
    assert any("高德" in n for n in notes)
    names = [p["name"] for p in payload["data"]["pois"]]
    assert "高德候选点" in names


@pytest.mark.skipif(
    __import__("os").getenv("TRAVEL_AMAP_LIVE_SMOKE") != "1",
    reason="live 冒烟需显式选择（AMAP_KEY + 网络可用；当前执行环境 httpx"
           " TLS 被沙箱拦截，真实样本录制 BLOCKED-IN-ENV）",
)
def test_live_amap_search_record_shape():
    """真实高德冒烟：字段契约 + 脱敏断言（不落库，仅结构核验）。"""
    provider = AmapPOIProvider()
    pois = provider.search("厦门", limit=3)
    assert pois, "live 检索应返回候选（网络/凭据由门控保证）"
    for poi in pois:
        assert poi.source == "amap:live"
        assert poi.verification_status == "unverified"
        assert poi.lat and poi.lng
