"""旅游对话消息恢复 API：登录身份、租户隔离与替换式幂等写入。"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import travel as travel_route
from backend.memory.manager import memory_manager
from backend.memory.service import MemoryService


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(travel_route.router)
    return TestClient(app, raise_server_exceptions=False)


def _headers(tenant_id: str) -> dict[str, str]:
    return {
        "X-User-Id": "traveler-15",
        "X-Auth-Type": "jwt",
        "X-Tenant-Id": tenant_id,
    }


def test_travel_conversation_message_endpoints_require_identity() -> None:
    client = _client()
    path = "/travel/conversations/trip-1/messages"

    assert client.get(path).status_code == 401
    assert client.put(path, json={"messages": []}).status_code == 401


def test_message_snapshots_round_trip_in_tenant_scoped_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同用户跨租户不得共用物理 session；快照重复写入保持单份。"""
    store: dict[tuple[str, str], list[dict[str, str]]] = {}
    observed: list[tuple[str, str]] = []

    async def _replace(
        self: MemoryService,
        session_id: str,
        messages: list[dict[str, str]],
        user_id: str = "default",
    ) -> dict:
        observed.append((session_id, user_id))
        store[(session_id, user_id)] = list(messages)
        return {"saved": len(messages)}

    async def _get(
        self: MemoryService,
        session_id: str,
        user_id: str | None = None,
    ) -> dict:
        observed.append((session_id, user_id or ""))
        messages = store.get((session_id, user_id or ""), [])
        return {
            "session_id": session_id,
            "messages": [
                {"id": index, "role": message["role"],
                 "content": message["content"], "created_at": "2026-10-08T00:00:00+00:00"}
                for index, message in enumerate(messages, start=1)
            ],
        }

    monkeypatch.setattr(MemoryService, "replace_session_messages", _replace, raising=False)
    monkeypatch.setattr(MemoryService, "get_session_messages", _get)
    monkeypatch.setattr(memory_manager, "run_tool", lambda operation: asyncio.run(operation()))

    client = _client()
    path = "/travel/conversations/trip-1/messages"
    snapshot = {"messages": [
        {"role": "user", "content": "帮我安排杭州两天"},
        {"role": "assistant", "content": "已生成杭州行程草案"},
    ]}
    saved_a = client.put(path, headers=_headers("tenant-a"), json=snapshot)
    repeated_a = client.put(path, headers=_headers("tenant-a"), json=snapshot)
    saved_b = client.put(path, headers=_headers("tenant-b"), json={"messages": [
        {"role": "user", "content": "另一租户的内容"},
    ]})

    assert saved_a.status_code == repeated_a.status_code == saved_b.status_code == 200
    assert saved_a.json()["saved"] == repeated_a.json()["saved"] == 2
    assert observed[0][0] == observed[1][0]
    assert observed[0][0] != observed[2][0]
    assert observed[0][1] == observed[1][1]
    assert observed[0][1] != observed[2][1]
    assert all(user_id.startswith("travel:") and len(user_id) <= 64
               for _, user_id in observed)

    restored_a = client.get(path, headers=_headers("tenant-a"))
    restored_b = client.get(path, headers=_headers("tenant-b"))
    assert [item["content"] for item in restored_a.json()["messages"]] == [
        "帮我安排杭州两天", "已生成杭州行程草案",
    ]
    assert [item["content"] for item in restored_b.json()["messages"]] == [
        "另一租户的内容",
    ]


def test_message_snapshot_rejects_unknown_roles() -> None:
    response = _client().put(
        "/travel/conversations/trip-1/messages",
        headers=_headers("tenant-a"),
        json={"messages": [{"role": "system", "content": "不可接受"}]},
    )

    assert response.status_code == 422
