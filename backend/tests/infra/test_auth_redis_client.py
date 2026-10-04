"""认证 Redis 与业务缓存 Redis 的隔离契约。"""

from __future__ import annotations

import sys
import types


def _reset_auth_redis_state(monkeypatch):
    import backend.infra.redis.client as client

    monkeypatch.setattr(client, "_auth_client", None, raising=False)
    monkeypatch.setattr(client, "_last_auth_probe_fail", 0.0, raising=False)
    return client


def test_auth_redis_uses_dedicated_url_and_does_not_reuse_cache_client(monkeypatch):
    client = _reset_auth_redis_state(monkeypatch)
    calls: list[dict] = []

    class FakeRedisClient:
        def ping(self):
            return True

    class FakeRedisModule:
        @staticmethod
        def from_url(url, **kwargs):
            calls.append({"url": url, "kwargs": kwargs})
            return FakeRedisClient()

    monkeypatch.setitem(sys.modules, "redis", types.SimpleNamespace(
        Redis=FakeRedisModule,
    ))
    monkeypatch.setattr("backend.config.redis.AUTH_REDIS_ENABLED", True,
                        raising=False)
    monkeypatch.setattr("backend.config.redis.AUTH_REDIS_URL",
                        "redis://auth-redis:6379/0", raising=False)
    monkeypatch.setattr("backend.config.redis.REDIS_MAX_CONNECTIONS", 17)
    monkeypatch.setattr("backend.config.redis.REDIS_SOCKET_TIMEOUT", 3)

    cache_client = object()
    monkeypatch.setattr(client, "_client", cache_client)

    auth_client = client.get_auth_redis()

    assert auth_client is not cache_client
    assert calls[0]["url"] == "redis://auth-redis:6379/0"
    assert calls[0]["kwargs"]["max_connections"] == 17
    assert calls[0]["kwargs"]["socket_timeout"] == 3


def test_auth_session_write_uses_auth_redis(monkeypatch):
    import backend.app.api.routes.auth_local as auth_local

    class FakeRedis:
        def __init__(self):
            self.values: dict[str, str] = {}
            self.set_calls: list[tuple] = []

        def set(self, key, value, ex=None):
            self.values[key] = value
            self.set_calls.append((key, value, ex))
            return True

        def sadd(self, key, value):
            return 1

        def expire(self, key, ttl):
            return True

    redis = FakeRedis()
    monkeypatch.setattr(auth_local, "get_auth_redis", lambda: redis,
                        raising=False)
    monkeypatch.setattr(auth_local.time, "time", lambda: 1_000)

    assert auth_local._write_session({
        "jti": "jti-1",
        "sid": "sid-1",
        "userId": 7,
        "exp": 1_600,
    }) is True
    assert redis.values["auth:session:7:jti-1"] == "1"
