"""AgentHub（坐席 WS 推送）单元测试 — 2026-09-17 人工介入 v2。

覆盖：ticket 一次性核销/过期、广播送达、死连接清理、无连接静默丢弃。
P3.2 增补：统一封套（event_id/seq/ts）、Redis 可用时不本进程双发、
落库失败 seq=null 仍广播、Redis 不可用降级本进程广播。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from backend.customer_service.realtime import TICKET_TTL_SECONDS, AgentHub


class _FakeWS:
    """最小 WebSocket 桩：只实现 Hub 用到的 send_text。"""

    def __init__(self, fail: bool = False):
        self.sent: list[str] = []
        self._fail = fail

    async def send_text(self, data: str) -> None:
        if self._fail:
            raise RuntimeError("connection closed")
        self.sent.append(data)


def _bare_hub() -> AgentHub:
    """带主 loop 但不起 Redis 订阅线程的 Hub（单元测试口径）。"""
    hub = AgentHub()
    hub.bind_loop(asyncio.get_running_loop(), start_subscriber=False)
    return hub


async def _no_redis(self, data: str) -> bool:
    """桩：模拟 Redis 不可用 → 返回 False 走本进程广播降级。"""
    return False


async def _redis_ok(self, data: str) -> bool:
    """桩：模拟 Redis 发布成功（由订阅线程转发，本进程不再广播）。"""
    return True


async def _no_persist(self, payload, envelope):
    """桩：跳过 events 落库（单测不依赖 DB），seq 保持 None。"""
    _persist_calls.append(envelope["type"])
    return None


async def _no_outbox(self, envelope, payload):
    """默认不触碰真实 Redis，避免单测产生持久化补偿消息。"""
    return None


_persist_calls: list[str] = []


@pytest.fixture(autouse=True)
def _stub_persist(monkeypatch, request):
    """默认跳过 events 落库；Redis 路径用例单独覆盖 _publish_via_redis。
    _persist_calls 记录落库请求（清空于每用例前），供断言调用与否。"""
    _persist_calls.clear()
    if request.node.name == (
        "test_persist_event_propagates_database_failure_to_compensation_layer"
    ):
        yield
        return
    monkeypatch.setattr(AgentHub, "_persist_event", _no_persist)
    monkeypatch.setattr(AgentHub, "_publish_via_redis", _no_redis)
    monkeypatch.setattr(AgentHub, "_enqueue_event_outbox", _no_outbox)
    yield


# ── ticket 鉴权 ──────────────────────────────────────────


async def test_ticket_roundtrip_single_use():
    hub = AgentHub()
    ticket = hub.issue_ticket()
    assert hub.redeem_ticket(ticket) is True
    # 一次性：第二次核销失败
    assert hub.redeem_ticket(ticket) is False


async def test_ticket_invalid_and_expired():
    hub = AgentHub()
    assert hub.redeem_ticket("no-such-ticket") is False
    ticket = hub.issue_ticket()
    # 人为置为过期
    hub._tickets[ticket] = 0.0
    assert hub.redeem_ticket(ticket) is False


def test_ticket_ttl_constant():
    assert TICKET_TTL_SECONDS == 60


# ── 基础广播 ─────────────────────────────────────────────


async def test_publish_broadcasts_to_all_live_connections():
    hub = _bare_hub()
    ws1, ws2 = _FakeWS(), _FakeWS()
    hub._connections.update({ws1, ws2})

    hub.publish("conversation.waiting", item={"conversation_id": "c1"})

    # run_coroutine_threadsafe 需要让主 loop 跑一拍
    await asyncio.sleep(0.05)

    for ws in (ws1, ws2):
        assert len(ws.sent) == 1
        event = json.loads(ws.sent[0])
        assert event["type"] == "conversation.waiting"
        assert event["item"]["conversation_id"] == "c1"


async def test_publish_drops_dead_connections():
    hub = _bare_hub()
    dead, live = _FakeWS(fail=True), _FakeWS()
    hub._connections.update({dead, live})

    hub.publish("message.created", conversation_id="c1", last_id=1)
    await asyncio.sleep(0.05)

    assert hub.connection_count == 1  # 死连接已被清理
    assert len(live.sent) == 1
    assert dead.sent == []


async def test_publish_without_loop_is_noop():
    """未 bind_loop：无法投递任何东西，静默丢弃（不落库不广播）。"""
    hub = AgentHub()
    ws = _FakeWS()
    hub._connections.add(ws)
    hub.publish("conversation.claimed", conversation_id="c1")
    await asyncio.sleep(0.01)
    assert ws.sent == []
    assert _persist_calls == []


async def test_publish_without_connections_still_persists():
    """无坐席在线：不广播，但事件照常落库（补发源完整性，2026-09-18 优化）。"""
    hub = _bare_hub()
    hub.publish("message.created", conversation_id="c1", last_id=1)
    await asyncio.sleep(0.05)
    assert _persist_calls == ["message.created"]


# ── P3.2：统一封套 ───────────────────────────────────────


async def test_envelope_has_event_id_seq_ts():
    """封套三字段：event_id（uuid hex）/ seq（无落库为 None）/ ts（ISO）。"""
    hub = _bare_hub()
    ws = _FakeWS()
    hub._connections.add(ws)

    hub.publish("message.created", conversation_id="c1", last_id=7)
    await asyncio.sleep(0.05)

    event = json.loads(ws.sent[0])
    assert len(event["event_id"]) == 32
    int(event["event_id"], 16)  # 合法 hex
    assert event["seq"] is None  # 落库被桩 → seq=None 仍广播
    assert "T" in event["ts"]  # ISO8601
    # 原有平铺 payload 保留（向后兼容）
    assert event["conversation_id"] == "c1"
    assert event["last_id"] == 7


async def test_event_id_unique_per_publish():
    """两次 publish 的 event_id 不同（客户端幂等键不冲突）。"""
    hub = _bare_hub()
    ws = _FakeWS()
    hub._connections.add(ws)

    hub.publish("message.created", conversation_id="c1", last_id=1)
    await asyncio.sleep(0.05)
    hub.publish("message.created", conversation_id="c1", last_id=2)
    await asyncio.sleep(0.05)

    ids = [json.loads(m)["event_id"] for m in ws.sent]
    assert len(ids) == 2 and ids[0] != ids[1]


# ── P3.2：Redis 路径 ─────────────────────────────────────


async def test_redis_available_skips_local_broadcast(monkeypatch):
    """Redis 发布成功 → 本进程不再直接广播（订阅线程转发，防双发）。"""
    monkeypatch.setattr(AgentHub, "_publish_via_redis", _redis_ok)
    hub = _bare_hub()
    ws = _FakeWS()
    hub._connections.add(ws)

    hub.publish("message.created", conversation_id="c1")
    await asyncio.sleep(0.05)

    assert ws.sent == []


async def test_persist_failure_still_broadcasts(monkeypatch):
    """落库失败 → seq=None，广播不受影响（事件尽力而为原则）。"""
    async def _boom(self, payload, envelope):
        raise RuntimeError("db down")

    monkeypatch.setattr(AgentHub, "_persist_event", _boom)
    hub = _bare_hub()
    ws = _FakeWS()
    hub._connections.add(ws)

    hub.publish("conversation.waiting", conversation_id="c1")
    await asyncio.sleep(0.05)

    event = json.loads(ws.sent[0])
    assert event["type"] == "conversation.waiting"
    assert event["seq"] is None


async def test_persist_failure_enqueues_event_for_compensation(monkeypatch):
    """PG 故障时事件仍广播，同时必须留下可恢复的补偿记录。"""
    queued: list[tuple[dict, dict]] = []

    async def _boom(self, payload, envelope):
        raise RuntimeError("db down")

    async def _queue(self, envelope, payload):
        queued.append((envelope, payload))

    monkeypatch.setattr(AgentHub, "_persist_event", _boom)
    # 新方法尚未存在时允许测试继续运行，断言会因没有调用而失败。
    monkeypatch.setattr(
        AgentHub, "_enqueue_event_outbox", _queue, raising=False,
    )
    monkeypatch.setattr(AgentHub, "_publish_via_redis", _no_redis)

    hub = _bare_hub()
    ws = _FakeWS()
    hub._connections.add(ws)

    hub.publish("conversation.waiting", conversation_id="c1")
    await asyncio.sleep(0.05)

    assert len(queued) == 1
    envelope, payload = queued[0]
    assert envelope["event_id"]
    assert envelope["seq"] is None
    assert payload["conversation_id"] == "c1"


async def test_persist_event_propagates_database_failure_to_compensation_layer(
    monkeypatch,
):
    """底层事件表写失败不能被 _persist_event 吞掉。"""
    from backend.customer_service.repository.event_repo import EventRepository

    async def _boom(self, **_kwargs):
        raise RuntimeError("postgres unavailable")

    monkeypatch.setattr(EventRepository, "append", _boom)

    hub = AgentHub()
    with pytest.raises(RuntimeError, match="postgres unavailable"):
        await hub._persist_event(
            {"conversation_id": "c1"},
            {"event_id": "event-1", "type": "message.created"},
        )


# ── 2026-09-18：瞬态事件 + 并发广播 ──────────────────────


async def test_transient_event_skips_persist():
    """persist=False（typing 类）：只广播不落库，不占 seq。"""
    hub = _bare_hub()
    ws = _FakeWS()
    hub._connections.add(ws)

    hub.publish("user.typing", conversation_id="c1", persist=False)
    await asyncio.sleep(0.05)

    assert _persist_calls == []
    event = json.loads(ws.sent[0])
    assert event["type"] == "user.typing"
    assert event["seq"] is None


async def test_broadcast_is_concurrent():
    """并发广播（确定性判定，不依赖计时）：首个 send 挂起等第二路进入——
    gather 并发下两路同时开始；若退回串行逐发，第二路永远不会开始，
    断言 entered==2 必红。"""
    hub = _bare_hub()
    gate = asyncio.Event()
    entered: list[bool] = []
    sent: list[str] = []

    class _GateWS:
        async def send_text(self, data: str) -> None:
            entered.append(True)
            if len(entered) == 1:
                # 第一路挂起，等第二路也进入 send（只有并发才可能发生）
                await asyncio.wait_for(gate.wait(), timeout=2.0)
            sent.append(data)

    ws1, ws2 = _GateWS(), _GateWS()
    hub._connections.update({ws1, ws2})

    hub.publish("message.created", conversation_id="c1")
    await asyncio.sleep(0.05)
    assert len(entered) == 2  # 串行实现：第一路仍阻塞在 gate，entered==1
    gate.set()
    await asyncio.sleep(0.05)
    assert len(sent) == 2
