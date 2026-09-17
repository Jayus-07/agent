"""tests/travel/test_providers.py — Provider 层（Phase 1，任务书 §3/§9）

覆盖四条主链路：
1. POI 时效标注：腾讯解析产物 unverified + observed_at；种子产物 verified
2. 远期出行日期强制降级：不发实时请求，本地估算 + fallback_reason
3. 实时路线打标：traffic_aware=True / is_estimate=False / observed_at
4. estimate_leg 签名兼容：旧式 4 参 provider 不受 trip_date 影响

纪律：所有 Provider 返回 dict 与 estimate_leg 同构，可直接构造 TransitLeg。
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.providers.travel.facts import (
    DEFAULT_FAR_TRIP_DAYS,
    FAR_TRIP_FALLBACK_REASON,
    UNVERIFIED,
    VERIFIED,
    is_far_trip,
    now_iso,
)
from backend.providers.travel.poi import SeedPOIProvider, TencentPOIProvider
from backend.providers.travel.transit import (
    LocalEstimateProvider,
    TencentTransitProvider,
)
from backend.tools.travel import routing
from backend.travel.models.itinerary import TransitLeg
from backend.travel.models.poi import Poi

# =============================================
# facts：远期判定与时间戳
# =============================================


class TestFarTrip:
    def test_none_date_is_not_far(self):
        assert is_far_trip(None) is False

    def test_recent_date_is_not_far(self):
        assert is_far_trip(date.today() + timedelta(days=3)) is False

    def test_beyond_horizon_is_far(self):
        assert is_far_trip(date.today() + timedelta(days=DEFAULT_FAR_TRIP_DAYS + 1)) is True

    def test_boundary_exactly_horizon_is_not_far(self):
        # 阈值语义：> horizon 才降级，边界当天仍可用实时数据
        assert is_far_trip(date.today() + timedelta(days=DEFAULT_FAR_TRIP_DAYS)) is False

    def test_now_iso_has_timezone(self):
        stamp = now_iso()
        assert "T" in stamp and ("+" in stamp or "Z" in stamp)


# =============================================
# POI Provider：时效标注
# =============================================


class TestSeedPOIProvider:
    def test_search_returns_verified_pois(self):
        provider = SeedPOIProvider()
        pois = provider.search("福州", limit=3)
        assert pois, "种子池应有福州候选"
        for p in pois:
            assert p.verification_status == VERIFIED

    def test_resolve_existing_name(self):
        provider = SeedPOIProvider()
        pois = provider.search("福州", limit=5)
        hit = provider.resolve(pois[0].name, "福州", required=True)
        assert hit is not None and hit.required is True

    def test_resolve_unknown_city_returns_none(self):
        assert SeedPOIProvider().resolve("随便", "亚特兰蒂斯") is None


class TestTencentPOIProvider:
    def _fake_hit(self, monkeypatch):
        """模拟腾讯 place_search 命中（不发真实请求）。"""
        hits = [{"id": "tx001", "name": "福道", "lat": 26.08,
                 "lng": 119.29, "category": "旅游景点:国家级景点", "city": "福州"}]
        monkeypatch.setattr(
            "backend.infra.lbs.api.place_search", lambda *a, **k: hits)
        monkeypatch.setattr(
            "backend.infra.lbs.api.resolve_district",
            lambda city: {"lat": 26.08, "lng": 119.29})
        monkeypatch.setattr(
            "backend.tools.travel.live_map.is_enabled", lambda: True)

    def test_resolve_marks_unverified(self, monkeypatch):
        self._fake_hit(monkeypatch)
        poi = TencentPOIProvider().resolve("福道", "福州", required=True)
        assert poi is not None
        assert poi.verification_status == UNVERIFIED
        assert poi.observed_at is not None
        assert poi.source == "tencent:lbs"
        assert poi.required is True

    def test_resolve_none_when_disabled(self, monkeypatch):
        monkeypatch.setattr(
            "backend.tools.travel.live_map.is_enabled", lambda: False)
        assert TencentPOIProvider().resolve("福道", "福州") is None

    def test_search_city_level_not_supported(self):
        # 城市级检索显式不支持（无营业时间的全量候选会让校验失真）
        assert TencentPOIProvider().search("福州") == []


# =============================================
# Transit Provider：远期降级 + 实时打标
# =============================================


class TestTencentTransitProvider:
    def _provider(self, monkeypatch, live_result):
        monkeypatch.setattr(
            "backend.tools.travel.live_map.is_enabled", lambda: True)
        monkeypatch.setattr(
            "backend.tools.travel.live_map.live_leg",
            lambda *a, **k: live_result)
        return TencentTransitProvider(far_trip_days=14)

    def test_far_trip_never_calls_live_api(self, monkeypatch):
        """远期日期：不发实时请求，本地估算 + 归因。"""
        calls = []

        def _spy(*a, **k):
            calls.append(a)
            return {"distance_km": 5.0, "mode": "drive", "minutes": 20,
                    "cost_cny": 25.0, "source": "tencent:lbs"}

        monkeypatch.setattr(
            "backend.tools.travel.live_map.is_enabled", lambda: True)
        monkeypatch.setattr(
            "backend.tools.travel.live_map.live_leg", _spy)
        provider = TencentTransitProvider(far_trip_days=14)

        far = date.today() + timedelta(days=45)
        est = provider.estimate(26.08, 119.29, 26.10, 119.31, trip_date=far)
        assert calls == [], "远期出行日期不得调用实时路况 API"
        assert est["source"] == routing.SOURCE_LOCAL
        assert est["is_estimate"] is True
        assert est["traffic_aware"] is False
        assert est["fallback_reason"] == FAR_TRIP_FALLBACK_REASON

    def test_near_trip_uses_live_and_marks_realtime(self, monkeypatch):
        near = date.today() + timedelta(days=2)
        live = {"distance_km": 5.0, "mode": "drive", "minutes": 20,
                "cost_cny": 25.0, "source": "tencent:lbs"}
        provider = self._provider(monkeypatch, live)
        est = provider.estimate(26.08, 119.29, 26.10, 119.31, trip_date=near)
        assert est["source"] == "tencent:lbs"
        assert est["is_estimate"] is False
        assert est["traffic_aware"] is True
        assert est["observed_at"] is not None
        assert est["fallback_reason"] is None

    def test_live_failure_returns_none_for_fallback(self, monkeypatch):
        """实时失败返回 None → estimate_leg 现有回落语义接管（旧契约不变）。"""
        provider = self._provider(monkeypatch, None)
        est = provider.estimate(26.08, 119.29, 26.10, 119.31,
                                trip_date=date.today())
        assert est is None

    def test_no_date_keeps_current_behavior(self, monkeypatch):
        """无出发日期：不触发远期策略，维持现状（实时可用）。"""
        live = {"distance_km": 5.0, "mode": "drive", "minutes": 20,
                "cost_cny": 25.0, "source": "tencent:lbs"}
        provider = self._provider(monkeypatch, live)
        est = provider.estimate(26.08, 119.29, 26.10, 119.31, trip_date=None)
        assert est["source"] == "tencent:lbs"


class TestLocalEstimateProvider:
    def test_estimate_shape_and_marks(self):
        est = LocalEstimateProvider().estimate(26.08, 119.29, 26.20, 119.40)
        assert est["source"] == routing.SOURCE_LOCAL
        assert est["is_estimate"] is True
        assert est["traffic_aware"] is False
        assert est["fallback_reason"] is None
        assert est["minutes"] > 0 and est["distance_km"] > 0


# =============================================
# estimate_leg：签名兼容与透传
# =============================================


class TestEstimateLegCompat:
    def setup_method(self):
        routing.set_route_provider(None)

    def teardown_method(self):
        routing.set_route_provider(None)

    def test_old_style_provider_unchanged(self, monkeypatch):
        """旧式 4 参 provider：不透传 trip_date，行为与 Phase 1 之前一致。"""
        seen = {}

        def old_provider(a, b, c, d):
            seen["args"] = (a, b, c, d)
            return {"distance_km": 1.0, "mode": "walk", "minutes": 10,
                    "cost_cny": 0.0, "source": "tencent:lbs"}

        routing.set_route_provider(old_provider)
        est = routing.estimate_leg(26.0, 119.0, 26.1, 119.1,
                                   trip_date=date.today())
        assert est["source"] == "tencent:lbs"
        assert seen["args"] == (26.0, 119.0, 26.1, 119.1)

    def test_provider_exception_falls_back_local(self, monkeypatch):
        def broken(*a, **k):
            raise RuntimeError("provider down")

        routing.set_route_provider(broken)
        est = routing.estimate_leg(26.0, 119.0, 26.1, 119.1)
        assert est["source"] == routing.SOURCE_LOCAL
        assert est["is_estimate"] is True

    def test_no_provider_local_marks(self):
        est = routing.estimate_leg(26.0, 119.0, 26.1, 119.1)
        assert est["is_estimate"] is True
        assert est["traffic_aware"] is False
        assert est["observed_at"] is not None


# =============================================
# 域模型：向后兼容与序列化（checkpointer 兼容性）
# =============================================


class TestModelFields:
    def test_transit_leg_from_provider_dict(self):
        leg = TransitLeg(
            from_title="A", to_title="B",
            distance_km=5.0, mode="drive", minutes=20, cost_cny=25.0,
            source="tencent:lbs", observed_at=now_iso(),
            traffic_aware=True, is_estimate=False, fallback_reason=None,
        )
        assert leg.traffic_aware is True

    def test_transit_leg_defaults_are_estimate(self):
        # 未标注的新 dict 字段缺省时按估算处理（保守披露）
        leg = TransitLeg(from_title="A", to_title="B", minutes=10)
        assert leg.is_estimate is True
        assert leg.traffic_aware is False
        assert leg.observed_at is None

    def test_poi_defaults_verified(self):
        poi = Poi(poi_id="x", name="X", city="福州", lat=26.0, lng=119.0)
        assert poi.verification_status == VERIFIED
        assert poi.observed_at is None

    def test_json_roundtrip_preserves_freshness(self):
        """时效字段必须能过 checkpointer 的 JSON 序列化往返。"""
        poi = Poi(poi_id="x", name="X", city="福州", lat=26.0, lng=119.0,
                  verification_status=UNVERIFIED, observed_at=now_iso())
        restored = Poi.model_validate_json(poi.model_dump_json())
        assert restored.verification_status == UNVERIFIED
        assert restored.observed_at == poi.observed_at
