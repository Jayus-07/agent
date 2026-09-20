"""P5 坐席 ws-ticket 与 WebSocket 身份契约测试。"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.websockets import WebSocketDisconnect

from backend.app.api.routes import cs_admin, cs_agent_ws
from backend.app.api.routes.cs_admin import issue_agent_ws_ticket


def _request(headers: dict[str, str]) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/cs/conversations/agent/ws-ticket",
        "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_ws_ticket_rejects_missing_tenant_identity() -> None:
    request = _request(
        {
            "X-Auth-Type": "jwt",
            "X-User-Id": "user-1",
        }
    )

    with pytest.raises(HTTPException) as error:
        await issue_agent_ws_ticket(request)

    assert error.value.status_code in {401, 403}


@pytest.mark.asyncio
async def test_ws_ticket_rejects_unbound_api_key_channel() -> None:
    request = _request(
        {
            "X-Auth-Type": "api-key",
            "X-API-Key": "service-key",
        }
    )

    with pytest.raises(HTTPException) as error:
        await issue_agent_ws_ticket(request)

    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_ws_ticket_does_not_accept_browser_agent_id(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(
        {
            "X-Auth-Type": "jwt",
            "X-User-Id": "user-1",
            "X-Tenant-Id": "tenant-a",
            "X-Roles": "admin",
        }
    )
    monkeypatch.setattr(
        "backend.customer_service.realtime.get_redis",
        lambda: None,
        raising=False,
    )

    with pytest.raises(HTTPException) as error:
        await issue_agent_ws_ticket(request)

    assert error.value.status_code in {401, 403, 503}


@pytest.mark.asyncio
async def test_ws_ticket_returns_503_when_redis_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(
        {
            "X-Auth-Type": "jwt",
            "X-User-Id": "agent-user-1",
            "X-Tenant-Id": "tenant-a",
            "X-Roles": "admin",
        }
    )
    async def resolve_agent(_request: Request) -> tuple[str, str]:
        return "agent-from-db", "tenant-a"

    monkeypatch.setattr(cs_admin, "_resolve_ws_agent_identity", resolve_agent)
    monkeypatch.setattr(
        "backend.customer_service.realtime.get_redis",
        lambda: None,
    )

    with pytest.raises(HTTPException) as error:
        await issue_agent_ws_ticket(request)

    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_ws_ticket_uses_server_side_agent_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(
        {
            "X-Auth-Type": "jwt",
            "X-User-Id": "agent-user-1",
            "X-Tenant-Id": "tenant-a",
        }
    )

    class Redis:
        def __init__(self) -> None:
            self.values: dict[str, tuple[int, str]] = {}

        def setex(self, key: str, ttl: int, value: str) -> bool:
            self.values[key] = (ttl, value)
            return True

    redis = Redis()
    async def resolve_agent(_request: Request) -> tuple[str, str]:
        return "agent-from-db", "tenant-a"

    monkeypatch.setattr(cs_admin, "_resolve_ws_agent_identity", resolve_agent)
    monkeypatch.setattr("backend.customer_service.realtime.get_redis", lambda: redis)

    response = await issue_agent_ws_ticket(request)

    assert response["ttl"] == 60
    assert response["ws_path"] == "/ws/cs/agent"
    assert len(redis.values) == 1
    payload = next(iter(redis.values.values()))[1]
    assert "agent-from-db" in payload
    assert "tenant-a" in payload


def test_ws_heartbeat_interval_is_15_seconds() -> None:
    assert cs_agent_ws._HEARTBEAT_INTERVAL_SECONDS == 15


@pytest.mark.asyncio
async def test_ws_route_passes_redeemed_identity_to_hub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class WebSocket:
        def __init__(self) -> None:
            self.accepted = False
            self.sent: list[dict[str, object]] = []
            self.closed: list[int] = []

        async def accept(self) -> None:
            self.accepted = True

        async def close(self, code: int) -> None:
            self.closed.append(code)

        async def send_json(self, data: dict[str, object]) -> None:
            self.sent.append(data)

        async def receive_text(self) -> str:
            raise WebSocketDisconnect(code=1000)

    class Hub:
        def __init__(self) -> None:
            self.connection_args: dict[str, str] | None = None
            self.disconnected = False

        def redeem_ticket_claims(self, token: str) -> dict[str, str] | None:
            assert token == "ticket-1"
            return {"agent_id": "agent-a", "tenant_id": "tenant-a"}

        def refresh_presence(self, *, agent_id: str, tenant_id: str) -> bool:
            assert (agent_id, tenant_id) == ("agent-a", "tenant-a")
            return True

        async def connect(self, websocket: WebSocket, *, agent_id: str, tenant_id: str) -> None:
            await websocket.accept()
            self.connection_args = {
                "agent_id": agent_id,
                "tenant_id": tenant_id,
            }

        @property
        def connection_count(self) -> int:
            return 1

        def disconnect(self, _websocket: WebSocket) -> None:
            self.disconnected = True

    hub = Hub()
    websocket = WebSocket()
    monkeypatch.setattr(
        "backend.customer_service.realtime.get_agent_hub", lambda: hub
    )

    await cs_agent_ws.cs_agent_ws(websocket, ticket="ticket-1")

    assert websocket.accepted is True
    assert hub.connection_args == {"agent_id": "agent-a", "tenant_id": "tenant-a"}
    assert hub.disconnected is True
