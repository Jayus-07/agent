"""tests/travel/test_transit_option.py — 公交/地铁候选（验收 #41）

覆盖两条验收面：
1. transit 响应解析：腾讯 direction(transit) 归一结果 → transit_option dict
   （duration_min/distance_m/summary/is_estimate=False）
2. 失败降级：接口失败/未启用/远期日期/缓存 miss → 无候选，排程照常

铁律：公交是候选展示，不改主路线口径 —— 组装处只读预热缓存
（peek_leg_transit），绝不现场等网络；TransitLeg.transit_option 缺省
None 保证向后兼容（旧 checkpoint 加载不受影响）。
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.config import map as map_cfg
from backend.tools.travel import live_map
from backend.travel.models.brief import TravelBrief
from backend.travel.models.itinerary import TransitLeg, TransitOption
from backend.travel.models.poi import Poi
from backend.travel.services import transit_service

# 腾讯 direction(transit) 的归一化返回形态（infra/lbs/api.py 契约；
# 实测结构：WALKING 段=distance+instruction，TRANSIT 段=lines[] 线路信息）
_TRANSIT_ROUTE = {
    "mode": "TRANSIT",
    "distance_m": 9772,
    "distance_km": 9.772,
    "duration_min": 45.0,
    "duration_s": 2700,
    "price_cny": 3.0,
    "steps": [
        {
            "instruction": "", "road": "", "distance_m": 1116,
            "duration_min": 17.0, "direction": "东南", "mode": "WALKING",
            "lines": [],
        },
        {
            "instruction": "", "road": "", "distance_m": 8181,
            "duration_min": 19.0, "direction": "", "mode": "TRANSIT",
            "lines": [{
                "vehicle": "subway", "title": "地铁2号线",
                "geton": "南门兜", "getoff": "鼓山", "station_count": 6,
            }],
        },
        {
            "instruction": "", "road": "", "distance_m": 475,
            "duration_min": 7.0, "direction": "东", "mode": "WALKING",
            "lines": [],
        },
    ],
}


@pytest.fixture(autouse=True)
def _enable_live_map(monkeypatch: pytest.MonkeyPatch):
    """本文件专测公交候选：开启 live map 并清空路段缓存（防用例间串扰）。

    三项齐开（is_configured = ENABLED and KEY；is_live_map_enabled 再与
    TRAVEL_USE_LIVE_MAP 求与）——根 conftest 把 ENABLED/USE_LIVE_MAP 都
    禁了，monkeypatch LIFO 保证本夹具的用例级覆盖生效。
    """
    monkeypatch.setattr(map_cfg, "TRAVEL_USE_LIVE_MAP", True)
    monkeypatch.setattr(map_cfg, "TENCENT_LBS_ENABLED", True)
    monkeypatch.setattr(map_cfg, "TENCENT_LBS_KEY", "test-key")
    live_map._LEG_CACHE.clear()
    yield
    live_map._LEG_CACHE.clear()


def _poi(name: str, lat: float, lng: float, **kw) -> Poi:
    return Poi(poi_id=f"test_{name}", name=name, city="福州", category="景点",
               lat=lat, lng=lng, open_time="08:00", close_time="18:00",
               suggested_minutes=60, ticket_cny=0.0, tags=[], rating=4.5,
               **kw)


def _brief() -> TravelBrief:
    return TravelBrief(destination="福州")


# =============================================
# 1. transit 响应解析
# =============================================


class TestTransitParse:
    def test_live_leg_transit_parses_normalized_route(self, monkeypatch):
        """归一化 transit 响应 → transit_option dict（时长/距离/摘要/非估算）。"""
        monkeypatch.setattr(live_map.api, "direction",
                            lambda mode, *a, **k: dict(_TRANSIT_ROUTE))
        result = live_map.live_leg_transit(26.08, 119.29, 26.05, 119.33)
        assert result is not None
        assert result["duration_min"] == 45
        assert result["distance_m"] == 9772
        assert result["is_estimate"] is False
        # 摘要来自 lines（地铁段）与步行距离：「… → 地铁2号线（南门兜 → 鼓山） → …」
        assert "地铁2号线（南门兜 → 鼓山）" in result["summary"]
        assert " → " in result["summary"]

    def test_summary_walk_distance_fallback(self):
        """无 lines 无 instruction 的段按步行距离兜底。"""
        steps = [{"distance_m": 300, "lines": []}]
        assert live_map._transit_summary(steps) == "步行 300m"

    def test_summary_caps_length_and_segments(self):
        """超过 4 段截断，总长 100 字封顶。"""
        steps = [{"lines": [{"vehicle": "subway", "title": f"{i}号线",
                             "geton": "甲站", "getoff": "乙站", "station_count": 3}]}
                 for i in range(6)]
        summary = live_map._transit_summary(steps)
        assert summary.count("号线") == 4  # 超过 4 段截断
        assert len(summary) <= 100

    def test_summary_empty_steps_returns_empty(self):
        """steps 空/无内容时摘要为空串（不影响时长/距离字段）。"""
        assert live_map._transit_summary([]) == ""
        assert live_map._transit_summary([{}, {"lines": [], "distance_m": 0}]) == ""

    def test_cache_hit_avoids_second_call(self, monkeypatch):
        """同段二次调用命中 _LEG_CACHE（mode 维度），不再打 API。"""
        calls = []

        def _fake_direction(mode, *a, **k):
            calls.append(mode)
            return dict(_TRANSIT_ROUTE)

        monkeypatch.setattr(live_map.api, "direction", _fake_direction)
        first = live_map.live_leg_transit(26.08, 119.29, 26.05, 119.33)
        second = live_map.live_leg_transit(26.08, 119.29, 26.05, 119.33)
        assert first == second
        assert len(calls) == 1

    def test_main_and_transit_cache_do_not_collide(self, monkeypatch):
        """mode 维度隔离：主路线与公交候选同坐标互不覆盖（混键会顶掉主路线）。"""
        monkeypatch.setattr(live_map.api, "direction", lambda mode, *a, **k: (
            dict(_TRANSIT_ROUTE) if mode == "transit" else {
                "mode": "DRIVING", "distance_m": 5000, "distance_km": 5.0,
                "duration_min": 11.0, "duration_s": 660, "taxi_fare_cny": 15.0,
                "steps": [],
            }))
        main = live_map.live_leg(26.08, 119.29, 26.05, 119.33)
        transit = live_map.live_leg_transit(26.08, 119.29, 26.05, 119.33)
        assert main["mode"] == "drive"
        assert main["minutes"] == 11
        assert transit["duration_min"] == 45


# =============================================
# 2. 失败降级（无候选，行程照常）
# =============================================


class TestTransitDegradation:
    def test_direction_none_returns_no_option(self, monkeypatch):
        """接口失败（None）→ 无候选，不抛异常。"""
        monkeypatch.setattr(live_map.api, "direction", lambda *a, **k: None)
        assert live_map.live_leg_transit(26.08, 119.29, 26.05, 119.33) is None

    def test_direction_empty_route_returns_no_option(self, monkeypatch):
        """无路线结果（distance 缺失）→ 无候选。"""
        monkeypatch.setattr(live_map.api, "direction",
                            lambda *a, **k: {"mode": "TRANSIT", "steps": []})
        assert live_map.live_leg_transit(26.08, 119.29, 26.05, 119.33) is None

    def test_disabled_live_map_returns_no_option(self, monkeypatch):
        """live map 未启用 → 无候选（总闸口径）。"""
        monkeypatch.setattr(map_cfg, "TRAVEL_USE_LIVE_MAP", False)
        assert live_map.live_leg_transit(26.08, 119.29, 26.05, 119.33) is None
        assert live_map.peek_leg_transit(26.08, 119.29, 26.05, 119.33) is None

    def test_peek_miss_returns_none(self):
        """预热 miss（未预热/预算超时）→ 只读路径返回 None，不现场等网络。"""
        assert live_map.peek_leg_transit(26.08, 119.29, 26.05, 119.33) is None


# =============================================
# 3. 排程组装旁路（主路线口径零改动）
# =============================================


class TestScheduleDayAttach:
    def test_warmed_cache_attaches_transit_option(self, monkeypatch):
        """预热命中 → leg.transit_option 附加；主时间轴口径零改动。"""
        import time

        monkeypatch.setattr(live_map.api, "direction",
                            lambda mode, *a, **k: dict(_TRANSIT_ROUTE))

        def _pois():
            return [_poi("A点", 26.08, 119.29), _poi("B点", 26.05, 119.33)]

        # 模拟预热：直接把候选写进缓存（时间戳必须用真实 monotonic——
        # Windows 上 monotonic 是开机秒数，写 0.0 会被判过期）
        live_map._LEG_CACHE[live_map._leg_cache_key(
            26.08, 119.29, 26.05, 119.33, mode="transit")] = (
                time.monotonic(),
                {"duration_min": 45, "distance_m": 4200,
                 "summary": "步行 1116m → 地铁2号线（南门兜 → 鼓山）", "is_estimate": False})

        day = transit_service.schedule_day(_pois(), 1, None, _brief())
        assert len(day.legs) == 1
        option = day.legs[0].transit_option
        assert isinstance(option, TransitOption)
        assert option.duration_min == 45
        assert option.is_estimate is False

        # 主时间轴口径不变：同一批 POI 清掉候选缓存后重排，items 时刻表与
        # legs 的主路线字段（除 transit_option）逐字段一致 —— 候选是纯展示
        # 增强，不渗入排程。
        live_map._LEG_CACHE.clear()
        day_plain = transit_service.schedule_day(_pois(), 1, None, _brief())
        assert [i.model_dump() for i in day.items] == [i.model_dump() for i in day_plain.items]
        legs_a = [l.model_dump(exclude={"transit_option"}) for l in day.legs]
        legs_b = [l.model_dump(exclude={"transit_option"}) for l in day_plain.legs]
        assert legs_a == legs_b

    def test_far_trip_date_has_no_option(self, monkeypatch):
        """远期出行日期 → 不附加候选（伪实时事实拦截）。"""
        import time

        monkeypatch.setattr(live_map.api, "direction",
                            lambda mode, *a, **k: dict(_TRANSIT_ROUTE))
        live_map._LEG_CACHE[live_map._leg_cache_key(
            26.08, 119.29, 26.05, 119.33, mode="transit")] = (
                time.monotonic(),
                {"duration_min": 45, "distance_m": 4200,
                 "summary": "x", "is_estimate": False})

        far_date = date.today() + timedelta(days=30)
        day = transit_service.schedule_day(
            [_poi("A点", 26.08, 119.29), _poi("B点", 26.05, 119.33)],
            1, far_date, _brief(),
        )
        assert len(day.legs) == 1
        assert day.legs[0].transit_option is None

    def test_cache_miss_attaches_nothing(self):
        """预热 miss → 无候选且排程照常（降级无候选的验收形态）。"""
        day = transit_service.schedule_day(
            [_poi("A点", 26.08, 119.29), _poi("B点", 26.05, 119.33)],
            1, None, _brief(),
        )
        assert len(day.legs) == 1
        assert day.legs[0].transit_option is None

    def test_leg_default_none_is_backward_compatible(self):
        """TransitLeg 缺省 transit_option=None：旧 checkpoint/旧代码零影响。"""
        leg = TransitLeg(from_title="A", to_title="B", minutes=10)
        assert leg.transit_option is None
        # 旧 dict（无 transit_option 键）可直接 model_validate
        legacy = TransitLeg.model_validate({
            "from_title": "A", "to_title": "B", "minutes": 10,
            "distance_km": 2.0, "mode": "drive", "cost_cny": 12.0,
            "source": "estimate:local",
        })
        assert legacy.transit_option is None


# =============================================
# 4. 预热：远期日期不发请求
# =============================================


class TestPrefetchTripDates:
    def test_far_trip_dates_skip_transit_prefetch(self, monkeypatch):
        """全部远期 → live_leg_transit 零调用（不发伪实时请求）。"""
        calls: list[tuple] = []

        def _fake_transit(*a):
            calls.append(a)
            return None

        monkeypatch.setattr(live_map, "live_leg_transit", _fake_transit)
        pois = [_poi("A点", 26.08, 119.29), _poi("B点", 26.05, 119.33)]
        far = date.today() + timedelta(days=30)
        transit_service.prefetch_day_legs([pois], trip_dates=[far])
        assert calls == []

    def test_near_trip_dates_prefetch_transit(self, monkeypatch):
        """近端日期 → transit 段进预热（best-effort）。"""
        calls: list[tuple] = []

        def _fake_transit(*a):
            calls.append(a)
            return None

        monkeypatch.setattr(live_map, "live_leg_transit", _fake_transit)
        pois = [_poi("A点", 26.08, 119.29), _poi("B点", 26.05, 119.33)]
        transit_service.prefetch_day_legs(
            [pois], trip_dates=[date.today() + timedelta(days=2)])
        assert len(calls) == 1

    def test_no_trip_dates_still_prefetches_transit(self, monkeypatch):
        """不传 trip_dates（未提供出发日期）→ transit 段照常预热。

        is_far_trip(None)=False 是既有语义：未提供日期不算远期，实时
        数据照常可用 —— 公交候选与主路线行为保持一致。
        """
        calls: list[tuple] = []

        def _fake_transit(*a):
            calls.append(a)
            return None

        monkeypatch.setattr(live_map, "live_leg_transit", _fake_transit)
        pois = [_poi("A点", 26.08, 119.29), _poi("B点", 26.05, 119.33)]
        transit_service.prefetch_day_legs([pois])
        assert len(calls) == 1
