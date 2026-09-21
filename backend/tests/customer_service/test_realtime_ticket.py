"""P5 Redis ticket、presence 与多实例定向广播契约测试。"""
from __future__ import annotations

import json
import time

import pytest

from backend.customer_service import realtime

AgentHub = realtime.AgentHub


class SharedRedis:
    """两个 Hub 共享的最小 Redis fake；不保存任何进程内 ticket。"""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expiry: dict[str, float] = {}

    def setex(self, key: str, ttl: int, value: str) -> bool:
        self.values[key] = value
        self.expiry[key] = time.monotonic() + ttl
        return True

    def getdel(self, key: str) -> str | None:
        if self.expiry.get(key, 0) <= time.monotonic():
            self.values.pop(key, None)
            self.expiry.pop(key, None)
            return None
        self.expiry.pop(key, None)
        return self.values.pop(key, None)

    def delete(self, key: str) -> int:
        existed = int(key in self.values)
        self.values.pop(key, None)
        self.expiry.pop(key, None)
        return existed

    def expire(self, key: str, ttl: int) -> bool:
        if key not in self.values:
            return False
        self.expiry[key] = time.monotonic() + ttl
        return True


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


def test_ticket_contract_constants_are_frozen() -> None:
    assert getattr(realtime, "TICKET_TTL_SECONDS", None) == 60
    assert getattr(realtime, "PRESENCE_TTL_SECONDS", None) == 45


def test_ticket_redeems_once_across_two_hub_instances(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = SharedRedis()
    monkeypatch.setattr("backend.customer_service.realtime.get_redis", lambda: redis)

    issuer = AgentHub()
    redeemer = AgentHub()
    ticket = issuer.issue_ticket(agent_id="agent-a", tenant_id="tenant-a")

    claims = redeemer.redeem_ticket_claims(ticket)

    assert claims == {"agent_id": "agent-a", "tenant_id": "tenant-a"}
    assert redeemer.redeem_ticket_claims(ticket) is None
    assert all(not key.startswith("agent-hub:ticket:") for key in issuer.__dict__)


def test_redis_failure_does_not_issue_in_memory_ticket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.customer_service.realtime.get_redis", lambda: None)
    hub = AgentHub()

    assert hub.issue_ticket(agent_id="agent-a", tenant_id="tenant-a") is None
    assert hub.redeem_ticket_claims("not-issued") is None


def test_presence_refresh_uses_redis_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = SharedRedis()
    monkeypatch.setattr("backend.customer_service.realtime.get_redis", lambda: redis)
    hub = AgentHub()

    assert hub.refresh_presence(agent_id="agent-a", tenant_id="tenant-a") is True
    key = hub.presence_key("tenant-a", "agent-a")
    assert key in redis.values
    assert redis.expiry[key] > time.monotonic()


@pytest.mark.asyncio
async def test_targeted_broadcast_is_scoped_by_tenant_and_agent() -> None:
    hub = AgentHub()
    ws_a = FakeWebSocket()
    ws_same_agent_other_tenant = FakeWebSocket()
    ws_b = FakeWebSocket()
    hub.register_connection(ws_a, agent_id="agent-a", tenant_id="tenant-a")
    hub.register_connection(
        ws_same_agent_other_tenant,
        agent_id="agent-a",
        tenant_id="tenant-b",
    )
    hub.register_connection(ws_b, agent_id="agent-b", tenant_id="tenant-a")

    await hub._broadcast(
        json.dumps(
            {
                "type": "conversation.waiting",
                "target_agent_id": "agent-b",
                "tenant_id": "tenant-a",
            }
        )
    )

    assert ws_a.sent == []
    assert ws_same_agent_other_tenant.sent == []
    assert len(ws_b.sent) == 1


@pytest.mark.asyncio
async def test_untargeted_broadcast_keeps_current_broadcast_semantics() -> None:
    hub = AgentHub()
    sockets = [FakeWebSocket(), FakeWebSocket()]
    for socket in sockets:
        hub.register_connection(socket, agent_id="agent", tenant_id="tenant")

    await hub._broadcast(json.dumps({"type": "conversation.waiting"}))

    assert all(len(socket.sent) == 1 for socket in sockets)
