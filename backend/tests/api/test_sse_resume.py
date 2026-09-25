"""F2 SSE Resume Protocol 契约测试。

覆盖（docs/2026-09-25-F2-SSE恢复协议-审计与设计.md §2/§三）：
  - 注册表语义：seq 单调、subscribe_after 原子重放+live 挂接（无丢洞）、
    gap 不可恢复、finished 清 waiters、身份归属、终帧必达
  - 端点契约：404 STREAM_NOT_RESUMABLE / 403 跨用户 / 422 非法游标 /
    重放含终端帧 / meta 帧 resume_supported + stream_id
  - 断连续播并集：慢消费者中途转 resume → 并集 seq 连续无重复，done 恰一次
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient


class _SlowFakeAgent:
    """依次产出 N 个 delta（间隔可调）+ done——可控的事件节奏。"""

    def __init__(self, n: int = 6, interval: float = 0.2):
        self.n = n
        self.interval = interval

    def stream_events(self, question, session_id, **kwargs):
        for i in range(self.n):
            time.sleep(self.interval)
            yield {"event": "delta", "data": {"content": f"c{i}"}}
        yield {"event": "done", "data": {"elapsed": 1.0, "sources": []}}


@pytest.fixture()
def client(monkeypatch):
    import backend.app.api.middleware.auth as auth_mw
    import backend.app.api.routes.chat as chat_mod
    from backend.app.api import stream_resume as sr_mod

    sr_mod.reset_stream_registry()
    monkeypatch.setattr(chat_mod, "get_multi_agent",
                        lambda: _SlowFakeAgent())
    monkeypatch.setattr(auth_mw, "API_KEY", "test")
    from backend.app.server import app
    with TestClient(app) as c:
        yield c
    sr_mod.reset_stream_registry()


def _parse_frames(body: str) -> list[dict]:
    """把 SSE 文本拆回帧（含 id 行与 data JSON）。"""
    frames: list[dict] = []
    cur: dict = {}
    for line in body.split("\n"):
        if line.startswith("id: "):
            cur["id"] = int(line[4:])
        elif line.startswith("event: "):
            cur["event"] = line[7:]
        elif line.startswith("data: ") and line[6:].strip():
            cur["data"] = json.loads(line[6:])
        elif line.strip() == "" and cur:
            frames.append(cur)
            cur = {}
    return frames


HDR = {"X-API-Key": "test"}
REQ = {"question": "断线恢复", "session_id": "sse-resume", "request_id": "sr-1"}


def test_stream_frames_carry_seq_and_resume_capability(client):
    with client.stream("POST", "/chat/stream", json=REQ, headers=HDR) as resp:
        body = b"".join(resp.iter_bytes()).decode("utf-8")
    frames = _parse_frames(body)

    meta = frames[0]
    assert meta["event"] == "meta"
    assert meta["data"]["resume_supported"] is True
    assert meta["data"]["stream_id"] == "sr-1"
    assert meta.get("id") == 1
    seqs = [f.get("id") for f in frames if "id" in f]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), "seq 必须单调唯一"
    assert frames[-1]["event"] == "done"
    assert frames[-1]["data"]["seq"] == seqs[-1], "终端帧也占 seq"


def test_resume_finished_stream_replays_tail_with_terminal(client):
    with client.stream("POST", "/chat/stream", json=REQ, headers=HDR) as resp:
        b"".join(resp.iter_bytes())

    with client.stream("POST", "/chat/stream/resume",
                       json={"request_id": "sr-1", "after_seq": 2},
                       headers=HDR) as resp:
        assert resp.status_code == 200
        body = b"".join(resp.iter_bytes()).decode("utf-8")
    frames = _parse_frames(body)
    seqs = [f["data"]["seq"] for f in frames]
    assert all(s > 2 for s in seqs), "只重放 after_seq 之后的事件"
    assert frames[-1]["event"] == "done", "重放尾必须含终端帧（不挂 live，立即返回）"


def test_resume_unknown_stream_404(client):
    resp = client.post("/chat/stream/resume",
                       json={"request_id": "no-such-stream", "after_seq": 0},
                       headers=HDR)
    assert resp.status_code == 404
    assert resp.json()["details"]["reason"] == "STREAM_NOT_RESUMABLE"


def test_resume_invalid_cursor_422(client):
    for bad in (-1, "x", None, 1.5):
        resp = client.post("/chat/stream/resume",
                           json={"request_id": "sr-x", "after_seq": bad},
                           headers=HDR)
        assert resp.status_code == 422, f"after_seq={bad!r} 应 422"


def test_resume_forbidden_for_other_identity(client, monkeypatch):
    """跨用户游标 403：注册表记录归属另一身份。"""
    from backend.app.api.stream_resume import get_stream_registry

    get_stream_registry().create(stream_id="sr-other", session_id="s",
                                 user_id="someone-else", tenant_id="other")
    resp = client.post("/chat/stream/resume",
                       json={"request_id": "sr-other", "after_seq": 0},
                       headers=HDR)
    assert resp.status_code == 403
    assert resp.json()["details"]["reason"] == "FORBIDDEN"


def test_resume_gap_returns_not_resumable(client, monkeypatch):
    from backend.app.api.stream_resume import get_stream_registry

    rec = get_stream_registry().create(stream_id="sr-gap", session_id="s",
                                       user_id="default", tenant_id="")
    # 模拟缓冲溢出：大量事件把头部挤出（4096 上限）
    for i in range(5000):
        rec.append({"event": "delta", "data": {"content": str(i)}})
    rec.finish()
    resp = client.post("/chat/stream/resume",
                       json={"request_id": "sr-gap", "after_seq": 1},
                       headers=HDR)
    assert resp.status_code == 404, "游标 1 已被挤出缓冲 → 不可恢复"
    assert resp.json()["details"]["reason"] == "STREAM_NOT_RESUMABLE"
    # 游标仍在缓冲内 → 可恢复
    ok = client.post("/chat/stream/resume",
                     json={"request_id": "sr-gap", "after_seq": 4990},
                     headers=HDR)
    assert ok.status_code == 200


def test_registry_subscribe_after_no_hole_under_concurrent_append():
    """追加与订阅并发：重放∪live 的 seq 必须连续无洞（原子挂接的证词）。"""
    from backend.app.api.stream_resume import StreamRecord

    rec = StreamRecord(stream_id="t", session_id="t", user_id="u", tenant_id="")
    for i in range(5):
        rec.append({"event": "delta", "data": {"i": i}})

    async def scenario():
        replay, gap, q = rec.subscribe_after(2)
        assert gap is False and [e.seq for e in replay] == [3, 4, 5]

        def pump():
            for i in range(6, 101):
                rec.append({"event": "delta", "data": {"i": i}})
                time.sleep(0.002)
            rec.finish()

        t = threading.Thread(target=pump)
        t.start()
        got = [e.seq for e in replay]
        while True:
            try:
                evt = await asyncio.wait_for(q.get(), timeout=5)
            except asyncio.TimeoutError:
                if rec.status == "finished":
                    break
                continue
            got.append(evt.seq)
            if evt.event.get("event") == "done" or (
                    rec.status == "finished" and evt.seq == 100):
                break
        t.join()
        return got

    got = asyncio.run(scenario())
    assert got == list(range(3, 101)), f"并集必须连续无洞无重复: {got[:5]}..{got[-5:]}"


def test_slow_consumer_union_complete_via_registry(client):
    """断线续播并集：中途脱离 → resume 续播 → 并集连续无洞、终端恰一次。

    注：TestClient 的 ASGI 传输整体缓冲响应体，无法在端点级模拟真·中途断流
    ——真断流语义由 F2.5 APISIX 实机驱动承担；本测试在注册表层验证
    「订阅前历史 + 订阅后 live」的并集不变量。
    """
    from backend.app.api import stream_resume as sr_mod

    sr_mod.reset_stream_registry()
    registry = sr_mod.get_stream_registry()
    rec = registry.create(stream_id="sr-live", session_id="s",
                          user_id="default", tenant_id="")
    for i in range(1, 4):  # 断开前已收到的 3 个事件（seq 1-3）
        rec.append({"event": "delta", "data": {"content": f"c{i}"}})

    async def scenario():
        replay, gap, q = rec.subscribe_after(3)
        assert gap is False and replay == []

        def producer_tail():
            for i in range(4, 9):
                time.sleep(0.01)
                rec.append({"event": "delta", "data": {"content": f"c{i}"}})
            rec.append({"event": "done", "data": {"elapsed": 1.0}})
            rec.finish()

        threading.Thread(target=producer_tail).start()
        seen: list[int] = []
        while True:
            evt = await asyncio.wait_for(q.get(), timeout=5)
            seen.append(evt.seq)
            if evt.event.get("event") == "done":
                return seen

    live_seqs = asyncio.run(scenario())
    # 「断开前已收」∪「resume 后 live」= 1..9 连续无洞（meta 后 delta 4-8 + done 9）
    assert sorted([1, 2, 3] + live_seqs) == list(range(1, 10))
    assert live_seqs[-1] == 9 and rec.status == "finished"
