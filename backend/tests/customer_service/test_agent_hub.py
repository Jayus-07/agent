"""AgentHub（坐席 WS 推送）单元测试 — 2026-09-17 人工介入 v2。

覆盖：ticket 一次性核销/过期、广播送达、死连接清理、无连接静默丢弃。
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


async def test_publish_broadcasts_to_all_live_connections():
    hub = AgentHub()
    hub.bind_loop(asyncio.get_running_loop())
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
    hub = AgentHub()
    hub.bind_loop(asyncio.get_running_loop())
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


def test_ticket_ttl_constant():
    assert TICKET_TTL_SECONDS == 60
