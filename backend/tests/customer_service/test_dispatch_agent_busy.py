"""dispatch/agent_busy.py — 自动置忙单元测试（fake redis，2026-09-21 治理）。

覆盖：拒单计数 → 阈值置忙 → 置忙查询 → Redis 故障 fail-open。
"""

from __future__ import annotations

import pytest

from backend.customer_service.dispatch import agent_busy


class FakeRedis:
    """最小 INCR/EXPIRE/SETEX/DELETE/MGET 语义，内存实现。"""

    def __init__(self) -> None:
        self.strings: dict[str, str] = {}

    def incr(self, key: str) -> int:
        value = int(self.strings.get(key, "0")) + 1
        self.strings[key] = str(value)
        return value

    def expire(self, key: str, _ttl: int) -> bool:
        return key in self.strings

    def setex(self, key: str, _ttl: int, value: str) -> bool:
        self.strings[key] = value
        return True

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if self.strings.pop(key, None) is not None:
                removed += 1
        return removed

    def mget(self, keys):
        return [self.strings.get(key) for key in keys]


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    client = FakeRedis()
    monkeypatch.setattr(agent_busy, "_redis_client", lambda: client)
    return client


@pytest.fixture()
def no_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_busy, "_redis_client", lambda: None)


async def test_threshold_rejects_trigger_busy_and_reset_counter(
    fake_redis: FakeRedis,
) -> None:
    results = [await agent_busy.record_agent_reject("t1", "agent-1") for _ in range(3)]

    assert results == [False, False, True]  # 第 3 次触发置忙
    assert fake_redis.strings[agent_busy.busy_key("t1", "agent-1")] == "1"
    # 计数器已清零，置忙过期后重新累计
    assert agent_busy.reject_count_key("t1", "agent-1") not in fake_redis.strings


async def test_busy_agent_ids_returns_only_busy_subset(
    fake_redis: FakeRedis,
) -> None:
    await agent_busy.record_agent_reject("t1", "agent-1")
    await agent_busy.record_agent_reject("t1", "agent-1")
    await agent_busy.record_agent_reject("t1", "agent-1")  # agent-1 置忙

    busy = await agent_busy.busy_agent_ids(
        tenant_id="t1", agent_ids=["agent-1", "agent-2"]
    )
    assert busy == {"agent-1"}


async def test_redis_unavailable_is_fail_open(no_redis: None) -> None:
    assert await agent_busy.record_agent_reject("t1", "agent-1") is False
    assert await agent_busy.busy_agent_ids(tenant_id="t1", agent_ids=["a"]) == set()


async def test_blank_ids_are_ignored(fake_redis: FakeRedis) -> None:
    assert await agent_busy.record_agent_reject("t1", "  ") is False
    assert await agent_busy.busy_agent_ids(tenant_id="t1", agent_ids=[None]) == set()
    assert fake_redis.strings == {}
