"""M3-d 通用 Tool 缓存层单测（纯函数级，走 InMemoryCache 降级路径）。"""
import json

import pytest

from backend.travel.services.tool_cache import _cache_key, cached_envelope


@pytest.fixture()
def _isolated_cache(monkeypatch):
    """隔离全局缓存实例 + 强制开关开启（不受 .env 影响）。"""
    import backend.infra.cache.backend as cache_backend
    import backend.config.travel as T

    monkeypatch.setattr(cache_backend, "_caches", {})
    monkeypatch.setattr(T, "TRAVEL_TOOL_CACHE_ENABLED", True)
    monkeypatch.setattr(T, "TRAVEL_TOOL_CACHE_TTL", 60)
    yield


def _ok(payload: dict) -> str:
    return json.dumps({"status": "success", "data": payload}, ensure_ascii=False)


def test_same_params_second_call_is_cache_hit(_isolated_cache):
    calls = []

    def invoke() -> str:
        calls.append(1)
        return _ok({"pois": [1, 2, 3]})

    raw1, hit1 = cached_envelope("map_place_search_tool", {"city": "福州", "keyword": "景区"}, invoke, ttl=60)
    raw2, hit2 = cached_envelope("map_place_search_tool", {"keyword": "景区", "city": "福州"}, invoke, ttl=60)
    assert hit1 is False and hit2 is True
    assert len(calls) == 1  # 第二次没打外部源
    assert json.loads(raw2)["data"]["pois"] == [1, 2, 3]


def test_failure_envelope_not_cached(_isolated_cache):
    calls = []

    def invoke() -> str:
        calls.append(1)
        return json.dumps({"status": "failed", "error": "timeout"}, ensure_ascii=False)

    cached_envelope("map_place_search_tool", {"city": "厦门"}, invoke, ttl=60)
    cached_envelope("map_place_search_tool", {"city": "厦门"}, invoke, ttl=60)
    assert len(calls) == 2  # 失败保留即时重试语义


def test_different_params_different_key(_isolated_cache):
    k1 = _cache_key("t", {"city": "福州"})
    k2 = _cache_key("t", {"city": "厦门"})
    k3 = _cache_key("t2", {"city": "福州"})
    assert len({k1, k2, k3}) == 3


def test_disabled_bypasses(_isolated_cache, monkeypatch):
    import backend.config.travel as T

    monkeypatch.setattr(T, "TRAVEL_TOOL_CACHE_ENABLED", False)
    calls = []

    def invoke() -> str:
        calls.append(1)
        return _ok({})

    cached_envelope("t", {}, invoke, ttl=60)
    cached_envelope("t", {}, invoke, ttl=60)
    assert len(calls) == 2
