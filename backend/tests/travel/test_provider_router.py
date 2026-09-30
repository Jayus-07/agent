"""tests/travel/test_provider_router.py — ProviderRouter 契约测试（Phase 4 Commit A）

覆盖（v4 §8 冻结纪律）：
- 链账完整性：PROVIDER_CHAINS 每条链非空、成员名可解析（含占位成员白名单）、
  weather 链与 capabilities.py 能力账对齐；
- 装配 parity：有和风 key → FallbackWeatherProvider(腾讯, 和风)；
  无 key → 裸 TencentWeatherProvider（与原硬编码工厂逐字节同构语义）；
- ttl_for/timeout_for 薄读口：读共享表/既有接线，不复制第二份表。

网络零依赖：装配只碰配置与构造器，不发请求。
"""
from __future__ import annotations

import pytest


# ---------- 链账完整性 ----------

def test_chains_nonempty_and_names_known():
    from backend.providers.travel.live.router import (
        _DECLARATION_ONLY,
        PROVIDER_CHAINS,
        _is_configured,
    )

    assert PROVIDER_CHAINS, "链账不得为空"
    for capability, chain in PROVIDER_CHAINS.items():
        assert chain, f"{capability} 链不得为空"
        for name in chain:
            # 每个成员名都必须可解析（占位成员恒可解析；真实成员查配置）
            assert _is_configured(name) in (True, False)
            assert name in _DECLARATION_ONLY or name in ("tencent", "qweather")


def test_weather_chain_declared_primary_first():
    from backend.providers.travel.live.router import PROVIDER_CHAINS

    chain = PROVIDER_CHAINS["weather.forecast"]
    assert chain[0] == "tencent", "链首恒主源（fallback 不跨语义）"
    assert "qweather" in chain


def test_weather_capability_aligns_with_capability_ledger():
    """链账 capability 命名与 capabilities.py 能力账对齐（G2：不造第二套名）。"""
    from backend.providers.travel.live.capabilities import get_capability
    from backend.providers.travel.live.router import PROVIDER_CHAINS

    assert get_capability("weather.forecast") is not None
    assert get_capability("maps.route") is not None
    assert "weather.forecast" in PROVIDER_CHAINS
    assert "maps.route" in PROVIDER_CHAINS


# ---------- 装配 parity（复刻原工厂两用例语义） ----------

@pytest.fixture
def weather_on(monkeypatch):
    from backend.config import travel as travel_cfg

    monkeypatch.setattr(travel_cfg, "TRAVEL_WEATHER_ENABLED", True)


def test_assemble_wraps_backup_when_key_configured(weather_on, monkeypatch):
    from backend.config import map as map_cfg
    from backend.providers.travel import live as L
    from backend.providers.travel.live.router import assemble_weather_provider

    monkeypatch.setattr(map_cfg, "QWEATHER_API_KEY", "test-key")
    assembled = assemble_weather_provider()
    assert isinstance(assembled, L.FallbackWeatherProvider)
    assert assembled.is_enabled()


def test_assemble_plain_tencent_without_key(weather_on, monkeypatch):
    from backend.config import map as map_cfg
    from backend.providers.travel import live as L
    from backend.providers.travel.live.router import assemble_weather_provider
    from backend.providers.travel.live.tencent import TencentWeatherProvider

    monkeypatch.setattr(map_cfg, "QWEATHER_API_KEY", "")
    assert isinstance(assemble_weather_provider(), TencentWeatherProvider)
    assert not isinstance(assemble_weather_provider(), L.FallbackWeatherProvider)


def test_factory_entry_still_routes_through_chain(weather_on, monkeypatch):
    """get_weather_provider 保持唯一工厂入口（消费方零改动），产物经链账装配。"""
    from backend.config import map as map_cfg
    from backend.providers.travel import live as L

    monkeypatch.setattr(L, "_SINGLETONS", {})
    monkeypatch.setattr(map_cfg, "QWEATHER_API_KEY", "test-key")
    assert isinstance(L.get_weather_provider(), L.FallbackWeatherProvider)

    monkeypatch.setattr(L, "_SINGLETONS", {})
    monkeypatch.setattr(map_cfg, "QWEATHER_API_KEY", "")
    assert not isinstance(L.get_weather_provider(), L.FallbackWeatherProvider)


def test_unknown_chain_member_fails_fast():
    """链账成员名不可解析 → fail-fast（G1 纪律，不许静默降级）。"""
    from backend.providers.travel.live import router as R

    with pytest.raises(KeyError):
        R._is_configured("不存在的源")
    with pytest.raises(KeyError):
        R._provider_instance("tencent")  # 主源不走实例工厂（恒链首直构）


# ---------- ttl_for / timeout_for 薄读口 ----------

def test_ttl_for_reads_shared_table():
    from backend.providers.travel.live.cache import FRESH_TTLS
    from backend.providers.travel.live.router import ttl_for

    for op, expected in (("place", 86400), ("route", 1800),
                         ("weather", 600), ("ticket", 600)):
        assert ttl_for(op) == expected == FRESH_TTLS[op]
    assert ttl_for("未登记操作") == 300  # 缺省回落与 cache_put_success 同款


def test_timeout_for_reads_existing_wiring(monkeypatch):
    from backend.config import travel as travel_cfg
    from backend.providers.travel.live.router import timeout_for

    monkeypatch.setattr(travel_cfg, "TRAVEL_WEATHER_TIMEOUT_S", 5.0)
    assert timeout_for("weather") == 5.0
    assert timeout_for("place") == 3.0  # 非 weather 走冻结表
    monkeypatch.setattr(travel_cfg, "TRAVEL_WEATHER_TIMEOUT_S", 0.2)
    assert timeout_for("weather") == 1.0  # resolve_budget 下限护栏保持
