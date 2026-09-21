"""P6 dispatcher 在线状态（Redis presence）契约测试。

presence 只做「候选是否活着」的判定，失败必须 fail-closed：返回 None
让调用方放弃本轮派单，绝不允许把 Redis 故障当成「无人在线」之外的
任何成功语义。
"""

from __future__ import annotations

import pytest

from backend.customer_service.dispatch import presence
from backend.customer_service.realtime import AgentHub


class _FakeRedis:
    """记录被查询的 key；模拟共享 Redis 实例的存在性查询。"""

    def __init__(self, present: set[str]) -> None:
        self.present = present
        self.mget_calls: list[list[str]] = []
        self.setex_calls: list[tuple[str, int, str]] = []
        self.raise_on_mget = False
        self.raise_on_setex = False

    def mget(self, keys):
        self.mget_calls.append(list(keys))
        if self.raise_on_mget:
            raise RuntimeError("redis down")
        return [b"1" if key in self.present else None for key in keys]

    def setex(self, key, ttl, value):  # pragma: no cover - 兼容备用路径
        self.setex_calls.append((key, ttl, value))
        if self.raise_on_setex:
            raise RuntimeError("redis down")
        self.present.add(key)
        return True

    # 无 pipeline 的客户端回落到逐 key exists
    def exists(self, key):  # pragma: no cover - 兼容备用路径
        return 1 if key in self.present else 0


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    client = _FakeRedis(set())
    monkeypatch.setattr(presence, "_redis_client", lambda: client)
    return client


def _key(tenant_id: str, agent_id: str) -> str:
    return AgentHub.presence_key(tenant_id, agent_id)


async def test_online_agent_ids_returns_only_present_keys(fake_redis: _FakeRedis) -> None:
    fake_redis.present = {_key("tenant-a", "agent-2")}

    online = await presence.online_agent_ids(
        tenant_id="tenant-a", agent_ids=["agent-1", "agent-2", "agent-3"]
    )

    assert online == {"agent-2"}


async def test_online_agent_ids_uses_shared_presence_key_scheme(
    fake_redis: _FakeRedis,
) -> None:
    await presence.online_agent_ids(tenant_id="tenant:with:colon", agent_ids=["a:b"])

    query = fake_redis.mget_calls[0]
    assert query == [_key("tenant:with:colon", "a:b")]


async def test_online_agent_ids_does_not_mix_tenants(fake_redis: _FakeRedis) -> None:
    fake_redis.present = {_key("tenant-b", "agent-1")}

    online = await presence.online_agent_ids(tenant_id="tenant-a", agent_ids=["agent-1"])

    assert online == set()


async def test_online_agent_ids_does_not_mix_agent_and_tenant_boundaries(
    fake_redis: _FakeRedis,
) -> None:
    """tenant/agent 边界必须无歧义：'a:b'+'c' 不能命中 'a'+'b:c' 的 key。"""
    fake_redis.present = {_key("a", "b:c")}

    online = await presence.online_agent_ids(tenant_id="a:b", agent_ids=["c"])

    assert online == set()


async def test_online_agent_ids_fails_closed_when_redis_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(presence, "_redis_client", lambda: None)

    assert await presence.online_agent_ids(tenant_id="t", agent_ids=["a"]) is None


async def test_online_agent_ids_fails_closed_on_redis_error(
    fake_redis: _FakeRedis,
) -> None:
    fake_redis.raise_on_mget = True

    assert await presence.online_agent_ids(tenant_id="t", agent_ids=["a"]) is None


async def test_online_agent_ids_without_candidates_skips_redis(
    fake_redis: _FakeRedis,
) -> None:
    online = await presence.online_agent_ids(tenant_id="t", agent_ids=[])

    assert online == set()
    assert fake_redis.mget_calls == []


def test_dispatcher_heartbeat_key_is_per_instance() -> None:
    assert presence.dispatcher_heartbeat_key("inst-1") != presence.dispatcher_heartbeat_key(
        "inst-2"
    )


async def test_write_dispatcher_heartbeat_uses_configured_ttl(
    fake_redis: _FakeRedis,
) -> None:
    from backend.config.cs_dispatch import CS_DISPATCHER_HEARTBEAT_TTL_SECONDS

    assert await presence.write_dispatcher_heartbeat("inst-1") is True

    key, ttl, _value = fake_redis.setex_calls[0]
    assert key == presence.dispatcher_heartbeat_key("inst-1")
    assert ttl == CS_DISPATCHER_HEARTBEAT_TTL_SECONDS == 30


async def test_write_dispatcher_heartbeat_fails_closed_when_redis_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(presence, "_redis_client", lambda: None)

    assert await presence.write_dispatcher_heartbeat("inst-1") is False


async def test_write_dispatcher_heartbeat_fails_closed_on_redis_error(
    fake_redis: _FakeRedis,
) -> None:
    fake_redis.raise_on_setex = True

    assert await presence.write_dispatcher_heartbeat("inst-1") is False


async def test_write_dispatcher_heartbeat_rejects_blank_instance_id(
    fake_redis: _FakeRedis,
) -> None:
    assert await presence.write_dispatcher_heartbeat("   ") is False
    assert fake_redis.setex_calls == []
