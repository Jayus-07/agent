"""test_chat_stream_hardening.py — chat 路由加固回归（审查报告 #14/#15/#16）

覆盖：
  #14 服务端唯一 request_id：缺失/"default" 占位 → 服务端生成唯一 id，
      经 meta 事件 + X-Request-Id 头回传；显式 id 原样保留。
  #15 消费侧不再占用默认线程池：行为面由 heartbeat/error-protocol 既有
      契约测试守护（asyncio.Queue 路径全绿 = 心跳与事件透传未破坏）。
  #16 backpressure 收尾帧：_put_final_frame 白盒单测（等待腾位/stop 跳过/
      loop 关闭静默）。TestClient 的 asgi transport 缓冲无限，无法端到端
      模拟真实传输背压，故走白盒。
  #14 伴随行为：abort 在旧客户端未回传服务端 id 时按 session 前缀兜底。
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient


class _EchoAgent:
    """立即完成的 fake agent：1 delta + done。"""

    def stream_events(self, question, session_id, **kwargs):
        yield {"event": "delta", "data": {"content": "ok"}}
        yield {"event": "done", "data": {"elapsed": 0.0, "sources": []}}


@pytest.fixture()
def client(monkeypatch):
    import backend.app.api.middleware.auth as auth_mw
    import backend.app.api.routes.chat as chat_mod

    monkeypatch.setattr(chat_mod, "get_multi_agent", lambda: _EchoAgent())
    monkeypatch.setattr(auth_mw, "API_KEY", "test")
    from backend.app.server import app

    return TestClient(app)


def _parse_meta(body: str) -> dict:
    for frame in body.split("\n\n"):
        if frame.startswith("event: meta"):
            data_line = next(l for l in frame.split("\n") if l.startswith("data: "))
            return json.loads(data_line[len("data: "):])
    raise AssertionError("meta 帧缺失")


class TestServerRequestId:
    """#14：request_id 唯一化与回传"""

    def test_missing_id_gets_server_generated_and_echoed(self, client):
        with client.stream(
            "POST", "/chat/stream",
            json={"question": "hi", "session_id": "rid-test"},  # 不传 request_id
            headers={"X-API-Key": "test"},
        ) as resp:
            assert resp.status_code == 200
            server_id = resp.headers.get("X-Request-Id")
            body = b"".join(resp.iter_bytes()).decode("utf-8")

        assert server_id, "X-Request-Id 头必须回传"
        assert server_id != "default", "服务端不得使用 'default' 占位 id"
        assert _parse_meta(body).get("request_id") == server_id, "meta 事件须回传同一 id"

    def test_default_placeholder_also_gets_unique_id(self, client):
        """旧客户端显式传 "default"（schema 旧默认值）也必须唯一化"""
        with client.stream(
            "POST", "/chat/stream",
            json={"question": "hi", "session_id": "rid-test", "request_id": "default"},
            headers={"X-API-Key": "test"},
        ) as resp:
            server_id = resp.headers.get("X-Request-Id")
            body = b"".join(resp.iter_bytes()).decode("utf-8")

        assert server_id and server_id != "default"
        assert _parse_meta(body)["request_id"] == server_id

    def test_explicit_id_preserved(self, client):
        with client.stream(
            "POST", "/chat/stream",
            json={"question": "hi", "session_id": "rid-test", "request_id": "my-rid-42"},
            headers={"X-API-Key": "test"},
        ) as resp:
            assert resp.headers.get("X-Request-Id") == "my-rid-42"
            body = b"".join(resp.iter_bytes()).decode("utf-8")

        assert _parse_meta(body)["request_id"] == "my-rid-42"

    def test_two_requests_get_distinct_ids(self, client):
        ids = []
        for _ in range(2):
            with client.stream(
                "POST", "/chat/stream",
                json={"question": "hi", "session_id": "rid-test"},
                headers={"X-API-Key": "test"},
            ) as resp:
                ids.append(resp.headers.get("X-Request-Id"))
        assert ids[0] and ids[1] and ids[0] != ids[1], "并发流不得共享 request_id（互踩根因）"


class TestFinalFrameDelivery:
    """#16：收尾帧（error/sentinel）在队列满时的投递语义（白盒）。

    TestClient 的 asgi transport 缓冲无限、send 永不阻塞，无法端到端模拟
    真实传输背压 → 直接单测 _put_final_frame（producer 背压分支/异常分支/
    sentinel 收尾都走它）。
    """

    @staticmethod
    def _start_loop() -> asyncio.AbstractEventLoop:
        loop = asyncio.new_event_loop()
        threading.Thread(target=loop.run_forever, daemon=True).start()
        return loop

    def test_waits_for_space_then_delivers(self):
        """队列满时等待 consumer 腾位，error 帧最终入队"""
        import backend.app.api.routes.chat as chat_mod

        aq: asyncio.Queue = asyncio.Queue(maxsize=1)
        aq.put_nowait({"event": "delta"})  # 填满
        loop = self._start_loop()
        try:
            # consumer：0.05s 取走一个
            asyncio.run_coroutine_threadsafe(self._drain_one(aq, delay=0.05), loop)
            t0 = time.monotonic()
            chat_mod._put_final_frame(aq, loop, threading.Event(),
                                      {"event": "error", "data": {"message": "bp"}}, wait=2.0)
            assert time.monotonic() - t0 >= 0.04, "队列满时必须等待腾位而非立即丢帧"
            got = asyncio.run_coroutine_threadsafe(self._snapshot(aq), loop).result(timeout=2)
            assert any(e.get("event") == "error" for e in got), "error 帧必须最终入队"
        finally:
            loop.call_soon_threadsafe(loop.stop)

    def test_stop_event_skips_waiting(self):
        """stop_event 已置（客户端断开）→ 不等待，尽力投递即返回"""
        import backend.app.api.routes.chat as chat_mod

        aq: asyncio.Queue = asyncio.Queue(maxsize=1)
        aq.put_nowait({"event": "delta"})  # 满
        loop = self._start_loop()
        try:
            stop = threading.Event()
            stop.set()
            t0 = time.monotonic()
            chat_mod._put_final_frame(aq, loop, stop, {"event": "error"}, wait=5.0)
            assert time.monotonic() - t0 < 1.0, "stop 已置时不得空等 5s"
        finally:
            loop.call_soon_threadsafe(loop.stop)

    def test_closed_loop_is_silent_noop(self):
        """loop 已关闭 → 静默放弃（不向已关 loop 投回调崩溃）"""
        import backend.app.api.routes.chat as chat_mod

        loop = asyncio.new_event_loop()
        loop.close()
        aq: asyncio.Queue = asyncio.Queue(maxsize=1)
        # 不应抛 RuntimeError
        chat_mod._put_final_frame(aq, loop, threading.Event(), {"event": "error"}, wait=0.1)

    @staticmethod
    async def _drain_one(aq: asyncio.Queue, delay: float) -> None:
        await asyncio.sleep(delay)
        if not aq.empty():
            aq.get_nowait()

    @staticmethod
    async def _snapshot(aq: asyncio.Queue) -> list:
        await asyncio.sleep(0.1)  # 等 call_soon_threadsafe 回调执行
        items = []
        while not aq.empty():
            items.append(aq.get_nowait())
        return items


class TestAbortSessionFallback:
    """#14 伴随：abort 精确 key 未命中时按 session 前缀兜底"""

    def test_abort_falls_back_to_session_prefix(self, client):
        import backend.app.api.routes.chat as chat_mod

        evt = threading.Event()
        chat_mod._active_stops["fb-sess:server-generated-uuid"] = evt
        try:
            # 旧客户端不回传服务端 id → 传 "default"，应命中前缀兜底
            resp = client.post(
                "/chat/abort",
                json={"session_id": "fb-sess", "request_id": "default"},
                headers={"X-API-Key": "test"},
            )
        finally:
            chat_mod._active_stops.pop("fb-sess:server-generated-uuid", None)

        assert resp.status_code == 200
        assert resp.json()["status"] == "aborted"
        assert evt.is_set(), "前缀兜底必须触发 stop_event"

    def test_abort_not_found_when_no_active_stream(self, client):
        resp = client.post(
            "/chat/abort",
            json={"session_id": "no-such-sess", "request_id": "default"},
            headers={"X-API-Key": "test"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_found"
