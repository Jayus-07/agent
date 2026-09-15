"""test_chat_heartbeat.py — SSE 心跳保活契约（P0：~97s 空闲断流止血）

用 fake agent（stream_events 静默 > 心跳间隔）验证：
  - 静默期产出 ping 事件（节流：间隔内至多一条）
  - 真实事件照常透传，done 完整到达（final_answer 不再被断流吞掉）
  _SSE_PING_INTERVAL 打桩到 0.8s，producer 静默 2.5s——总时长 ~3s。
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient


class _FakeAgent:
    """stream_events 静默 2.5s 后只发一个 done——制造 > 心跳间隔的事件空窗。"""

    def stream_events(self, question, session_id, **kwargs):
        time.sleep(2.5)
        yield {"event": "delta", "data": {"content": "晚到的回答"}}
        yield {"event": "done", "data": {"elapsed": 2.5, "sources": []}}


@pytest.fixture()
def client(monkeypatch):
    import backend.app.api.middleware.auth as auth_mw
    import backend.app.api.routes.chat as chat_mod

    monkeypatch.setattr(chat_mod, "_SSE_PING_INTERVAL", 0.8)
    monkeypatch.setattr(chat_mod, "get_multi_agent", lambda: _FakeAgent())
    # API-Key 中间件从 config 常量取值（模块级 import），打桩为与请求头一致
    monkeypatch.setattr(auth_mw, "API_KEY", "test")
    from backend.app.server import app

    return TestClient(app)


def test_silence_emits_pings_and_final_answer_survives(client):
    with client.stream(
        "POST", "/chat/stream",
        json={"question": "长任务", "session_id": "hb-test", "request_id": "hb-1"},
        headers={"X-API-Key": "test"},
    ) as resp:
        assert resp.status_code == 200
        body = b"".join(resp.iter_bytes()).decode("utf-8")

    ping_count = body.count("event: ping")
    assert ping_count >= 2, f"静默 2.5s 应产出 ≥2 条 ping（间隔 0.8s），实际 {ping_count}：\n{body[:500]}"
    assert "event: delta" in body, "真实事件必须照常透传"
    assert "event: done" in body, "done 必须完整到达（断流修复的核心断言）"


def test_ping_throttled_not_flooding(client):
    with client.stream(
        "POST", "/chat/stream",
        json={"question": "长任务", "session_id": "hb-test", "request_id": "hb-2"},
        headers={"X-API-Key": "test"},
    ) as resp:
        body = b"".join(resp.iter_bytes()).decode("utf-8")

    # 静默 2.5s、间隔 0.8s → 2~3 条；若按 q.get 0.5s 轮次发会 ≥4 条（洪水回归）
    ping_count = body.count("event: ping")
    assert ping_count <= 3, f"ping 必须按 interval 节流，实际 {ping_count} 条疑似洪水"
