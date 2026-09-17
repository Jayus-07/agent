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
    return None


@pytest.fixture(autouse=True)
def _stub_persist(monkeypatch):
    """默认跳过 events 落库；Redis 路径用例单独覆盖 _publish_via_redis。"""
    monkeypatch.setattr(AgentHub, "_persist_event", _no_persist)
    monkeypatch.setattr(AgentHub, "_publish_via_redis", _no_redis)


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


async def test_publish_without_loop_or_connections_is_noop():
    hub = AgentHub()
    # 无连接：不抛异常
    hub.publish("conversation.closed", conversation_id="c1")
    # 有连接但未 bind_loop：静默丢弃
    ws = _FakeWS()
    hub._connections.add(ws)
    hub.publish("conversation.claimed", conversation_id="c1")
    await asyncio.sleep(0.01)
    assert ws.sent == []


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
