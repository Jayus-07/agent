"""tests/travel/commerce/conftest.py — Commerce 测试隔离（STOP K）

两条隔离（沿 STOP J 测试纪律；本文件为 commerce 子包自有 conftest，
**不修改父级 conftest.py**——父级 fixture 依然按 pytest 层级可用）：

1. Provider 共享缓存换进程内空实例（生产 backend 是 Redis/TwoTierCache，
   单测直连会把测试数据写进共享栈、用例间互相污染——实测教训）；
2. quota 计数复位（进程内计数器跨用例残留会让「先查后增」断言漂移）。

config 开关用 monkeypatch 打 **config 模块属性**（config 在 import 时读
env 并固化常量，测 env 无效——与 config/travel.py 同机制）。
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest


@pytest.fixture
def isolated_provider_cache(monkeypatch):
    """Provider 共享缓存 → 进程内空实例（STOP J 同款隔离，commerce 复制）。"""
    from backend.infra.cache.backend import InMemoryCache
    from backend.providers.travel.live import cache as pcache

    store = InMemoryCache(default_ttl=3600)
    monkeypatch.setattr(pcache, "_backend", lambda: store)
    yield store


@pytest.fixture(autouse=True)
def reset_quota_counters():
    """每用例复位进程内 quota 计数（防跨用例残留）。"""
    from backend.providers.travel.live import quota

    quota.reset_local_counters()
    yield
    quota.reset_local_counters()


@pytest.fixture
def fake_mode(monkeypatch, isolated_provider_cache):
    """mode=fake + 白名单 + 域开关全开（commerce 全链路测试基座）。"""
    from backend.config import travel_commerce as cfg

    monkeypatch.setattr(cfg, "TRAVEL_COMMERCE_ENABLED", True)
    monkeypatch.setattr(cfg, "TRAVEL_COMMERCE_PROVIDER_MODE", "fake")
    monkeypatch.setattr(
        cfg, "TRAVEL_COMMERCE_DEEPLINK_ALLOWED_HOSTS",
        ("fake-commerce.example.com",))
    monkeypatch.setattr(cfg, "TRAVEL_COMMERCE_MAX_OFFERS", 10)
    return cfg


@pytest.fixture
def off_mode(monkeypatch, isolated_provider_cache):
    """mode=off（默认态）：prefilter 不命中、service disabled。"""
    from backend.config import travel_commerce as cfg

    monkeypatch.setattr(cfg, "TRAVEL_COMMERCE_ENABLED", False)
    monkeypatch.setattr(cfg, "TRAVEL_COMMERCE_PROVIDER_MODE", "off")
    return cfg


@pytest.fixture
def future_dates():
    """未来入住/退房（避开 check_in >= today 校验的日期相对性）。"""
    check_in = date.today() + timedelta(days=30)
    return check_in, check_in + timedelta(days=2)
