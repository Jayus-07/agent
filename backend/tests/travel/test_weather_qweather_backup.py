"""tests/travel/test_weather_qweather_backup.py — 和风天气备用源单测（2026-09-28）

覆盖：QWeatherProvider 冻结形状映射（与 api.weather_for_city future 同构）/
语义分类（鉴权/未收录/脏数据）/ stale-if-error / FallbackWeatherProvider
主备降级语义（主源优先、双败保主因、备用源未配置不降级）/ 工厂按配置装配。
网络一律经 transport 注入 mock，不发真实请求；缓存键用独立城市名防串扰。
"""
from __future__ import annotations

import pytest

from backend.providers.travel.live import cache as pcache
from backend.providers.travel.live import FallbackWeatherProvider
from backend.providers.travel.live.qweather import QWeatherProvider
from backend.providers.travel.live.result import ProviderStatus

# ---------- 造数 ----------

GEO_OK = {"code": "200", "location": [
    {"name": "映射城", "id": "101230101", "lat": "26.08", "lon": "119.30"},
]}
DAILY_OK = {"code": "200", "daily": [
    {"fxDate": "2026-09-29", "textDay": "中雨", "textNight": "阴",
     "tempMax": "28", "tempMin": "22", "windDirDay": "东北风", "windScaleDay": "3"},
    {"fxDate": "2026-09-30", "textDay": "晴", "textNight": "晴",
     "tempMax": "30", "tempMin": "24"},
]}


def _make_transport(geo_body, daily_body):
    """(url, params) -> body dict 的假传输层；记录调用序列供断言。"""
    calls: list[tuple[str, dict]] = []

    def transport(path: str, params: dict) -> dict:
        calls.append((path, dict(params)))
        return dict(geo_body) if path == "/geo/v2/city/lookup" else dict(daily_body)

    return transport, calls


@pytest.fixture
def qweather_on(monkeypatch):
    """启用天气总闸 + 和风 Key（provider 内部按调用时读模块全局）。"""
    from backend.config import map as map_cfg
    from backend.config import travel as travel_cfg

    monkeypatch.setattr(travel_cfg, "TRAVEL_WEATHER_ENABLED", True)
    monkeypatch.setattr(map_cfg, "QWEATHER_API_KEY", "test-key")
    yield


# ---------- QWeatherProvider ----------

def test_forecast_maps_to_frozen_shape(qweather_on):
    transport, calls = _make_transport(GEO_OK, DAILY_OK)
    r = QWeatherProvider(transport=transport).forecast_payload("映射城")

    assert r.ok, r.error
    assert r.provider == "qweather"
    d = r.data
    assert d["kind"] == "future"
    assert d["city"] == "映射城"
    assert [rec["date"] for rec in d["days"]] == ["2026-09-29", "2026-09-30"]
    # day/night 走冻结键名（weather 专家只认 "weather"，其余字段对齐腾讯口径）
    assert d["days"][0]["day"]["weather"] == "中雨"
    assert d["days"][0]["night"]["weather"] == "阴"
    assert d["days"][0]["day"]["temperature"] == "28"
    assert d["days"][0]["night"]["temperature"] == "22"
    assert d["days"][0]["day"]["wind_direction"] == "东北风"
    # 调用顺序：先城市定位，后 7d 预报（location 用定位返回的 id）
    assert [c[0] for c in calls] == ["/geo/v2/city/lookup", "/v7/weather/7d"]
    assert calls[1][1]["location"] == "101230101"


def test_disabled_when_master_switch_off(monkeypatch):
    from backend.config import travel as travel_cfg

    monkeypatch.setattr(travel_cfg, "TRAVEL_WEATHER_ENABLED", False)

    def _no_call(path, params):
        raise AssertionError("总闸关闭时不应发起任何调用")

    r = QWeatherProvider(transport=_no_call).forecast_payload("某城")
    assert r.status == ProviderStatus.DISABLED


def test_auth_failure_maps_to_unauthorized(qweather_on):
    r = QWeatherProvider(transport=lambda p, q: {"code": "401"}) \
        .forecast_payload("鉴权城")
    assert r.status == ProviderStatus.UNAUTHORIZED


def test_unknown_city_maps_to_not_found(qweather_on):
    r = QWeatherProvider(
        transport=lambda p, q: {"code": "200", "location": []},
    ).forecast_payload("不存在城")
    assert r.status == ProviderStatus.NOT_FOUND


def test_empty_daily_maps_to_invalid_response(qweather_on):
    def transport(path, params):
        return dict(GEO_OK) if path == "/geo/v2/city/lookup" else {"code": "200"}

    r = QWeatherProvider(transport=transport).forecast_payload("脏数据城")
    assert r.status == ProviderStatus.INVALID_RESPONSE


def test_stale_cache_served_on_failure(qweather_on, monkeypatch):
    from backend.providers.travel.live import cache as pcache_mod

    # fresh TTL 打成 -1：写入即过期，无需等真实 TTL
    monkeypatch.setattr(pcache_mod, "FRESH_TTLS", {"weather": -1})
    transport, _ = _make_transport(GEO_OK, DAILY_OK)
    p = QWeatherProvider(transport=transport)
    assert p.forecast_payload("防震城").ok  # 先成功一次，写入缓存

    def _boom(path, params):
        raise ConnectionError("网络断了")

    p2 = QWeatherProvider(transport=_boom)
    r = p2.forecast_payload("防震城")
    assert r.ok and r.freshness.value == "stale"
    assert r.data["days"][0]["day"]["weather"] == "中雨"


# ---------- FallbackWeatherProvider ----------

class _StubProvider:
    def __init__(self, name, enabled, result):
        self.name = name
        self._enabled = enabled
        self._result = result
        self.calls = 0

    def is_enabled(self):
        return self._enabled

    def forecast_payload(self, city):
        self.calls += 1
        return self._result


def _ok(provider_name):
    from backend.providers.travel.live.result import success

    return success({"kind": "future", "days": [{"date": "2026-09-29"}]},
                   provider=provider_name, operation="weather")


def _fail(status, error=""):
    from backend.providers.travel.live.result import failure

    return failure(status, provider="stub", operation="weather", error=error)


def test_primary_success_short_circuits_backup():
    primary = _StubProvider("tencent:lbs", True, _ok("tencent:lbs"))
    backup = _StubProvider("qweather", True, _ok("qweather"))
    r = FallbackWeatherProvider(primary, backup).forecast_payload("某城")
    assert r.ok and r.provider == "tencent:lbs"
    assert backup.calls == 0


def test_falls_back_when_primary_fails():
    primary = _StubProvider("tencent:lbs", True,
                            _fail(ProviderStatus.UNAVAILABLE, "腾讯挂了"))
    backup = _StubProvider("qweather", True, _ok("qweather"))
    r = FallbackWeatherProvider(primary, backup).forecast_payload("某城")
    assert r.ok and r.provider == "qweather"
    assert primary.calls == 1 and backup.calls == 1


def test_both_fail_keeps_primary_cause():
    primary = _StubProvider("tencent:lbs", True,
                            _fail(ProviderStatus.UNAUTHORIZED, "主因：腾讯鉴权"))
    backup = _StubProvider("qweather", True,
                           _fail(ProviderStatus.TIMEOUT, "次因：和风超时"))
    r = FallbackWeatherProvider(primary, backup).forecast_payload("某城")
    assert r.status == ProviderStatus.UNAUTHORIZED
    assert "主因" in r.error


def test_backup_not_configured_returns_primary_failure():
    primary = _StubProvider("tencent:lbs", True,
                            _fail(ProviderStatus.RATE_LIMITED, "限流"))
    backup = _StubProvider("qweather", False, _ok("qweather"))
    r = FallbackWeatherProvider(primary, backup).forecast_payload("某城")
    assert r.status == ProviderStatus.RATE_LIMITED
    assert backup.calls == 0


def test_primary_disabled_delegates_to_backup():
    primary = _StubProvider("tencent:lbs", False,
                            _fail(ProviderStatus.DISABLED, "未启用"))
    backup = _StubProvider("qweather", True, _ok("qweather"))
    r = FallbackWeatherProvider(primary, backup).forecast_payload("某城")
    assert r.ok and r.provider == "qweather"


def test_composite_disabled_when_all_disabled():
    primary = _StubProvider("tencent:lbs", False,
                            _fail(ProviderStatus.DISABLED))
    backup = _StubProvider("qweather", False, _fail(ProviderStatus.DISABLED))
    comp = FallbackWeatherProvider(primary, backup)
    assert not comp.is_enabled()
    assert comp.forecast_payload("某城").status == ProviderStatus.DISABLED


# ---------- 工厂装配 ----------

def test_factory_wraps_backup_when_key_configured(qweather_on, monkeypatch):
    from backend.providers.travel import live as L

    monkeypatch.setattr(L, "_SINGLETONS", {})
    p = L.get_weather_provider()
    assert isinstance(p, L.FallbackWeatherProvider)
    assert p.is_enabled()


def test_factory_plain_tencent_without_key(monkeypatch):
    from backend.config import map as map_cfg
    from backend.config import travel as travel_cfg
    from backend.providers.travel import live as L

    monkeypatch.setattr(L, "_SINGLETONS", {})
    monkeypatch.setattr(travel_cfg, "TRAVEL_WEATHER_ENABLED", True)
    monkeypatch.setattr(map_cfg, "QWEATHER_API_KEY", "")
    p = L.get_weather_provider()
    assert not isinstance(p, L.FallbackWeatherProvider)


def test_stale_helper_only_returns_expired_entries():
    """pcache 契约护栏：未过期条目不得被 stale-if-error 取走（§32）。

    唯一城市名防测试间串扰；条目随物理 TTL 自然过期，无需清理接口。
    """
    key = pcache.build_key("qweather:weather", "契约护栏城-仅本测试")
    pcache.cache_put_success(key, data={"kind": "future", "days": []},
                            provider="qweather", operation="weather",
                            observed_at="2026-09-28T00:00:00+08:00")
    assert pcache.cache_get_stale(key) is None
