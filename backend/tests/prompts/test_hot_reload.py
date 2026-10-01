"""Prompt Runtime epoch 与 Redis 热更新核心行为。"""
from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def reset_hot_reload_state():
    """每个测试独立重置进程内 epoch，避免单调 setter 污染后续用例。"""
    from backend.prompts import hot_reload

    hot_reload._local_epoch = 0
    hot_reload._last_epoch_check_at = 0.0
    hot_reload._last_refresh_at = 0.0
    yield
    hot_reload._local_epoch = 0
    hot_reload._last_epoch_check_at = 0.0
    hot_reload._last_refresh_at = 0


class FakeRedis:
    def __init__(self, epoch: int | None = None):
        self.values = {}
        if epoch is not None:
            self.values["agent:prompt:epoch"] = str(epoch)
        self.published: list[tuple[str, str]] = []

    def incr(self, key: str) -> int:
        value = int(self.values.get(key, "0")) + 1
        self.values[key] = str(value)
        return value

    def get(self, key: str):
        return self.values.get(key)

    def publish(self, channel: str, payload: str) -> int:
        self.published.append((channel, payload))
        return 1

    def set(self, key: str, value: str, ex: int | None = None):
        self.values[key] = value
        return True

    def scan_iter(self, match: str):
        prefix = match.rstrip("*")
        return (key for key in self.values if key.startswith(prefix))


@pytest.mark.asyncio
async def test_bump_prompt_epoch_increments_and_publishes(monkeypatch):
    from backend.prompts import hot_reload

    redis = FakeRedis()
    monkeypatch.setattr(hot_reload, "get_redis", lambda: redis)
    monkeypatch.setattr(hot_reload, "_persist_epoch_event", _persist_epoch(1))

    epoch = await hot_reload.bump_prompt_epoch("planner.system", actor="admin")

    assert epoch == 1
    assert redis.values[hot_reload.PROMPT_EPOCH_KEY] == "1"
    channel, raw = redis.published[0]
    assert channel == hot_reload.PROMPT_CHANGED_CHANNEL
    assert json.loads(raw)["epoch"] == 1
    assert json.loads(raw)["key"] == "planner.system"


@pytest.mark.asyncio
async def test_reload_event_refreshes_snapshot(monkeypatch):
    from backend.prompts import hot_reload

    refreshed = []

    async def refresh_snapshot():
        refreshed.append(True)

    monkeypatch.setattr(hot_reload.prompt_service, "refresh_snapshot", refresh_snapshot)
    hot_reload._set_local_epoch(1)

    await hot_reload._handle_reload_event({"epoch": 2, "key": "planner.system"})

    assert refreshed == [True]
    assert hot_reload.local_epoch() == 2


@pytest.mark.asyncio
async def test_ensure_fresh_refreshes_when_redis_epoch_is_newer(monkeypatch):
    from backend.prompts import hot_reload

    redis = FakeRedis(epoch=5)
    refreshed = []

    async def refresh_snapshot():
        refreshed.append(True)

    monkeypatch.setattr(hot_reload, "get_redis", lambda: redis)
    monkeypatch.setattr(hot_reload, "_read_db_epoch", lambda: _async_value(None))
    monkeypatch.setattr(hot_reload.prompt_service, "refresh_snapshot", refresh_snapshot)
    hot_reload._local_epoch = 3
    hot_reload._last_epoch_check_at = 0.0
    hot_reload._last_refresh_at = 0.0

    await hot_reload.ensure_prompt_snapshot_fresh_async()

    assert refreshed == [True]
    assert hot_reload.local_epoch() == 5


@pytest.mark.asyncio
async def test_redis_failure_is_fail_open(monkeypatch):
    from backend.prompts import hot_reload

    refreshed = []

    async def refresh_snapshot():
        refreshed.append(True)

    def broken_redis():
        raise ConnectionError("redis down")

    monkeypatch.setattr(hot_reload, "get_redis", broken_redis)
    monkeypatch.setattr(hot_reload.prompt_service, "refresh_snapshot", refresh_snapshot)
    hot_reload._local_epoch = 3
    hot_reload._last_epoch_check_at = 0.0

    await hot_reload.ensure_prompt_snapshot_fresh_async()

    assert refreshed == []
    assert hot_reload.local_epoch() == 3


def test_runtime_metadata_contains_epoch_versions_and_snapshot_context(monkeypatch):
    from backend.prompts import hot_reload

    hot_reload._local_epoch = 9
    monkeypatch.setattr(
        hot_reload.prompt_service,
        "snapshot_metadata",
        lambda: {
            "snapshot_time": "2026-09-30T00:00:00+00:00",
            "reload_source": "pubsub",
        },
    )

    metadata = hot_reload.prompt_runtime_metadata({"customer_service.query_intent": 2})

    assert metadata == {
        "epoch": 9,
        "versions": {"customer_service.query_intent": 2},
        "snapshot_time": "2026-09-30T00:00:00+00:00",
        "reload_source": "pubsub",
    }


def test_runtime_status_reports_process_heartbeat(monkeypatch):
    from backend.prompts import hot_reload

    redis = FakeRedis(epoch=12)
    monkeypatch.setattr(hot_reload, "get_redis", lambda: redis)

    result = hot_reload.runtime_status()

    assert result["epoch"] == 12
    assert result["processes"]
    assert result["processes"][0]["status"] == "healthy"


def test_pubsub_listener_uses_polling_timeout():
    from backend.prompts import hot_reload

    class FakePubSub:
        def __init__(self):
            self.calls = []

        def get_message(self, **kwargs):
            self.calls.append(kwargs)
            return None

    pubsub = FakePubSub()

    assert hot_reload._next_pubsub_message(pubsub) is None
    assert pubsub.calls == [
        {"ignore_subscribe_messages": True, "timeout": 1.0}
    ]


@pytest.mark.asyncio
async def test_ensure_fresh_uses_db_epoch_when_redis_lags(monkeypatch):
    from backend.prompts import hot_reload

    redis = FakeRedis(epoch=1)
    refreshed = []

    async def refresh_snapshot():
        refreshed.append(True)

    monkeypatch.setattr(hot_reload, "get_redis", lambda: redis)
    monkeypatch.setattr(hot_reload, "_read_db_epoch", lambda: _async_value(5))
    monkeypatch.setattr(hot_reload.prompt_service, "refresh_snapshot", refresh_snapshot)
    hot_reload._local_epoch = 1
    hot_reload._last_epoch_check_at = 0.0
    hot_reload._last_refresh_at = 0.0

    await hot_reload.ensure_prompt_snapshot_fresh_async()

    assert refreshed == [True]
    assert hot_reload.local_epoch() == 5


def _persist_epoch(epoch: int):
    async def _persist(*, key: str, actor: str):
        return epoch

    return _persist


def _async_value(value):
    async def _value():
        return value

    return _value()
