"""POST /chat/messages 使用服务端身份隔离持久 L2 写入。"""
from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import chat as chat_route


def _client() -> TestClient:
    app = FastAPI()
    app.add_api_route("/chat/messages", chat_route.save_messages, methods=["POST"])
    return TestClient(app, raise_server_exceptions=False)


def test_chat_message_persistence_rejects_guest():
    response = _client().post(
        "/chat/messages",
        json={"session_id": "s1", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 401


def test_chat_message_persistence_uses_trusted_tenant_and_user(monkeypatch):
    from backend.memory import manager as manager_module
    from backend.memory import service as service_module

    calls: list[dict] = []

    class FakeMemoryService:
        async def save_messages(self, **kwargs):
            calls.append(kwargs)
            return {"saved": len(kwargs["messages"])}

    monkeypatch.setattr(service_module, "MemoryService", FakeMemoryService)
    monkeypatch.setattr(
        manager_module.memory_manager,
        "run_tool",
        lambda coro_factory: asyncio.run(coro_factory()),
    )

    response = _client().post(
        "/chat/messages",
        headers={
            "X-User-Id": "trusted-user",
            "X-Tenant-Id": "tenant-a",
            "X-Auth-Type": "jwt",
        },
        json={
            "session_id": "s1",
            "user_id": "attacker-user",
            "tenant_id": "attacker-tenant",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 200
    assert response.json() == {"saved": 1}
    assert calls == [{
        "session_id": "s1",
        "messages": [{"role": "user", "content": "hi"}],
        "user_id": "trusted-user",
        "tenant_id": "tenant-a",
    }]
