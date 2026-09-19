"""P3 事件补偿队列测试。"""
from __future__ import annotations

import json

import pytest

from backend.customer_service import event_outbox


class _FakeRedis:
    def __init__(self, *, pending=None, fresh=None):
        self.pending = list(pending or [])
        self.fresh = list(fresh or [])
        self.added = []
        self.acked = []
        self.deleted = []
        self.groups = []

    def xgroup_create(self, stream, group, id="0-0", mkstream=False):
        self.groups.append((stream, group, id, mkstream))

    def xadd(self, stream, fields, maxlen=None, approximate=True):
        self.added.append((stream, fields, maxlen, approximate))
        return "3-0"

    def xautoclaim(
        self, stream, group, consumer, min_idle_time, start_id,
        count=None, justid=False,
    ):
        return "0-0", list(self.pending), []

    def xreadgroup(self, group, consumer, streams, count=None, block=0):
        if not self.fresh:
            return []
        values = list(self.fresh)
        self.fresh.clear()
        return [(next(iter(streams)), values)]

    def xack(self, stream, group, message_id):
        self.acked.append((stream, group, message_id))
        self.pending = [item for item in self.pending if item[0] != message_id]
        return 1

    def xdel(self, stream, message_id):
        self.deleted.append((stream, message_id))
        return 1


class _LegacyRedis(_FakeRedis):
    """只提供旧版 Redis pending API，模拟没有 XAUTOCLAIM 的环境。"""

    def xautoclaim(
        self, stream, group, consumer, min_idle_time, start_id,
        count=None, justid=False,
    ):
        raise AttributeError("xautoclaim is unavailable")

    def xpending_range(
        self, stream, group, min="-", max="+", count=None, consumername=None,
    ):
        return [
            {
                "message_id": message_id,
                "time_since_delivered": 120000,
            }
            for message_id, _fields in self.pending[:count]
        ]

    def xclaim(
        self, stream, group, consumer, min_idle_time, message_ids,
        idle=None, time=None, retrycount=None, force=False, justid=False,
    ):
        wanted = {str(message_id) for message_id in message_ids}
        return [
            item for item in self.pending if str(item[0]) in wanted
        ]


class _WorkerInterruptRedis(_FakeRedis):
    def __init__(self, *, interruptions: int, **kwargs):
        super().__init__(**kwargs)
        self.interruptions = interruptions

    def xack(self, stream, group, message_id):
        if self.interruptions:
            self.interruptions -= 1
            raise RuntimeError("worker interrupted after postgres commit")
        return super().xack(stream, group, message_id)


def _message(message_id="1-0", event_id="event-1"):
    event = {
        "event_id": event_id,
        "type": "message.created",
        "ts": "2026-09-19T14:00:00+00:00",
        "conversation_id": "conv-1",
        "payload": {"conversation_id": "conv-1", "last_id": 7},
    }
    return message_id, {"event": json.dumps(event, ensure_ascii=False)}


def test_enqueue_event_keeps_stable_event_id(monkeypatch):
    redis = _FakeRedis()
    monkeypatch.setattr(event_outbox, "_redis", lambda: redis, raising=False)

    result = event_outbox.enqueue_event(
        envelope={
            "event_id": "event-1",
            "type": "message.created",
            "ts": "2026-09-19T14:00:00+00:00",
        },
        payload={"conversation_id": "conv-1", "last_id": 7},
    )

    assert result is True
    assert len(redis.added) == 1
    record = json.loads(redis.added[0][1]["event"])
    assert record["event_id"] == "event-1"
    assert record["payload"]["conversation_id"] == "conv-1"


def test_drain_acks_only_after_postgres_commit(monkeypatch):
    redis = _FakeRedis(pending=[_message()])
    persisted = []
    monkeypatch.setattr(event_outbox, "_redis", lambda: redis, raising=False)
    monkeypatch.setattr(
        event_outbox,
        "_persist_event_record",
        lambda event: persisted.append(event) or 101,
        raising=False,
    )

    result = event_outbox.drain_pending_events(
        limit=10, min_idle_ms=0, consumer="worker-1",
    )

    assert result == {"ok": True, "processed": 1, "failed": 0}
    assert len(persisted) == 1
    assert len(redis.acked) == 1
    assert len(redis.deleted) == 1


def test_drain_reclaims_pending_with_legacy_redis_api(monkeypatch):
    redis = _LegacyRedis(pending=[_message()])
    persisted = []
    monkeypatch.setattr(event_outbox, "_redis", lambda: redis, raising=False)
    monkeypatch.setattr(
        event_outbox,
        "_persist_event_record",
        lambda event: persisted.append(event) or 104,
        raising=False,
    )

    result = event_outbox.drain_pending_events(
        limit=10, min_idle_ms=60000, consumer="legacy-worker",
    )

    assert result == {"ok": True, "processed": 1, "failed": 0}
    assert [event["event_id"] for event in persisted] == ["event-1"]
    assert len(redis.acked) == 1
    assert len(redis.deleted) == 1


def test_drain_recovers_after_twenty_worker_interruptions(monkeypatch):
    redis = _WorkerInterruptRedis(
        interruptions=20,
        pending=[_message()],
    )
    persisted_ids = set()
    persist_calls = 0
    monkeypatch.setattr(event_outbox, "_redis", lambda: redis, raising=False)

    def _persist(event):
        nonlocal persist_calls
        persist_calls += 1
        persisted_ids.add(event["event_id"])
        return 105

    monkeypatch.setattr(
        event_outbox, "_persist_event_record", _persist, raising=False,
    )

    for _ in range(20):
        result = event_outbox.drain_pending_events(
            limit=10, min_idle_ms=0, consumer="killed-worker",
        )
        assert result == {"ok": False, "processed": 0, "failed": 1}

    assert redis.acked == []
    assert redis.pending

    recovered = event_outbox.drain_pending_events(
        limit=10, min_idle_ms=0, consumer="recovered-worker",
    )

    assert recovered == {"ok": True, "processed": 1, "failed": 0}
    assert persist_calls == 21
    assert persisted_ids == {"event-1"}
    assert len(redis.acked) == 1
    assert len(redis.deleted) == 1


def test_drain_leaves_pending_on_db_failure_then_recovers(monkeypatch):
    redis = _FakeRedis(pending=[_message()])
    calls = []
    monkeypatch.setattr(event_outbox, "_redis", lambda: redis, raising=False)

    def _persist(event):
        calls.append(event["event_id"])
        if len(calls) == 1:
            raise RuntimeError("postgres unavailable")
        return 102

    monkeypatch.setattr(
        event_outbox, "_persist_event_record", _persist, raising=False,
    )

    first = event_outbox.drain_pending_events(
        limit=10, min_idle_ms=0, consumer="worker-1",
    )
    assert first == {"ok": False, "processed": 0, "failed": 1}
    assert redis.acked == []
    assert redis.pending

    second = event_outbox.drain_pending_events(
        limit=10, min_idle_ms=0, consumer="worker-2",
    )
    assert second == {"ok": True, "processed": 1, "failed": 0}
    assert len(redis.acked) == 1
    assert len(redis.deleted) == 1


def test_duplicate_event_id_is_delegated_to_idempotent_repository(monkeypatch):
    redis = _FakeRedis(
        fresh=[_message("2-0"), _message("2-1")],
    )
    persisted_ids = set()
    monkeypatch.setattr(event_outbox, "_redis", lambda: redis, raising=False)

    def _persist(event):
        persisted_ids.add(event["event_id"])
        return 103

    monkeypatch.setattr(
        event_outbox, "_persist_event_record", _persist, raising=False,
    )

    result = event_outbox.drain_pending_events(
        limit=10, min_idle_ms=0, consumer="worker-1",
    )

    assert result == {"ok": True, "processed": 2, "failed": 0}
    assert persisted_ids == {"event-1"}
    assert len(redis.acked) == 2


def test_drain_without_redis_is_explicitly_unavailable(monkeypatch):
    monkeypatch.setattr(event_outbox, "_redis", lambda: None, raising=False)

    assert event_outbox.drain_pending_events() == {
        "ok": False,
        "processed": 0,
        "failed": 0,
    }
