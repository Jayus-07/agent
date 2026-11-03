"""核心链路工程质量修复回归（审计 P2 批次第二轮，2026-11-03）。

钉死四件事：
  1. 任务流 SSE 订阅窗口补帧：入口快照与 subscribe 之间的终态事件丢失
     由订阅后重查 DB 权威状态兜底，客户端不再无限等待 heartbeat。
  2. 会话守卫锁过期释放不再把已成功提交的任务掩盖成 500
     （LockNotOwnedError = 锁 30s TTL 自然过期，属正常路径）。
  3. task_service 高频 with 场景池化（_pooled_conn：借出前探活）；
     _conn() 保留裸连接语义供事务所有权场景。
  4. 内网 embedding 自建 http client 显式继承 EMBEDDING_REQUEST_TIMEOUT。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


# ── 1. SSE 订阅窗口补帧 ──

@pytest.mark.asyncio
async def test_task_stream_supplements_terminal_frame_after_subscribe(
        monkeypatch):
    """快照非终态 + subscribe 后 DB 已终态：补终态帧并收口（不进心跳循环）。"""
    from backend.app.api.routes import tasks as tasks_route
    from backend.models.task import TaskStatus

    record = SimpleNamespace(status=TaskStatus.RUNNING,
                             to_public_dict=lambda: {"taskId": "t-1"})
    frames: list[str] = []

    class _FakeRequest:
        async def is_disconnected(self):
            return False

    def fake_subscribe(task_id):
        # subscribe_events 是同步接口；订阅建立瞬间 DB 权威状态已终态
        #（窗口内错过 pub/sub 事件）
        monkeypatch.setattr(
            tasks_route.task_service, "get_task",
            lambda _tid: SimpleNamespace(status=TaskStatus.SUCCESS))
        return SimpleNamespace(get_message=lambda timeout: None,
                               close=lambda: None)

    monkeypatch.setattr(tasks_route.task_manager, "subscribe_events",
                        fake_subscribe)

    # 身份/归属链桩：_identity 与 _get_owned_task
    monkeypatch.setattr(
        tasks_route, "_identity",
        lambda _req, _uid: SimpleNamespace(
            user_id="u-1", tenant_id="t", kind="user", actor="user:u-1"))
    monkeypatch.setattr(
        tasks_route, "_get_owned_task", lambda *_a, **_k: record)
    monkeypatch.setattr(tasks_route, "_tenant",
                        lambda *_a, **_k: "t")

    # 路由函数返回 StreamingResponse，事件 generator 在 body_iterator 里
    response = await tasks_route.stream_task_events(
        "t-1", _FakeRequest(), user_id="u-1")
    async for frame in response.body_iterator:
        frames.append(frame)
        if len(frames) > 20:  # 防御：补帧路径必须收口，不得进心跳循环
            break

    assert any("event: completed" in f for f in frames), frames
    assert any("event: done" in f for f in frames)
    assert not any("event: ping" in f for f in frames), (
        "已终态必须立即收口，不应进入心跳等待")


# ── 2. 会话守卫锁过期释放 ──

def test_conversation_guard_swallows_expired_lock(monkeypatch):
    """锁 30s TTL 自然过期后 release 抛 LockNotOwnedError：不得外泄成 500
    （守卫体内任务很可能已成功提交）。"""
    import hashlib as _hashlib

    from redis.exceptions import LockNotOwnedError

    from backend.app.api.routes import agent_chat_tasks as act

    class _ExpiredLock:
        def acquire(self):
            return True

        def release(self):
            raise LockNotOwnedError("lock expired")

    class _FakeRedis:
        def lock(self, _key, timeout=30, blocking_timeout=2):
            return _ExpiredLock()

    monkeypatch.setattr(act.task_manager, "_redis", lambda: _FakeRedis())
    ident = SimpleNamespace(tenant_id="t", user_id="u")

    with act._conversation_guard(ident, "sess-1"):
        pass  # 正常退出（finally 里的 release 抛错必须被吞掉）


def test_conversation_guard_reraises_body_errors(monkeypatch):
    """守卫体业务异常照常上抛（吞的只是锁过期，不掩盖真实业务失败）。"""
    from backend.app.api.routes import agent_chat_tasks as act

    class _OkLock:
        def acquire(self):
            return True

        def release(self):
            return None

    class _FakeRedis:
        def lock(self, _key, timeout=30, blocking_timeout=2):
            return _OkLock()

    monkeypatch.setattr(act.task_manager, "_redis", lambda: _FakeRedis())
    ident = SimpleNamespace(tenant_id="t", user_id="u")

    with pytest.raises(ValueError):
        with act._conversation_guard(ident, "sess-1"):
            raise ValueError("submit failed")


# ── 3/4. 结构断言（源码级防回退）──

def _src(rel: str) -> str:
    return (Path(__file__).resolve().parents[3] / rel).read_text(encoding="utf-8")


def test_task_service_uses_pool_for_with_scenarios():
    src = _src("backend/services/task_service.py")
    assert "def _pooled_conn()" in src
    assert "check=ConnectionPool.check_connection" in src, (
        "池必须带借出前探活（PG 重启自愈，同 pg_saver 模式）")
    assert "with _conn() as conn" not in src, (
        "with 场景应全部走 _pooled_conn()（心跳路径每 15s/任务新建握手，审计 P2）")
    assert "def _conn()" in src, (
        "_conn() 保留裸连接语义（事务所有权场景 managed_tasks 使用）")


def test_private_embedding_http_client_carries_timeout():
    src = _src("backend/rag/embedding_singleton.py")
    assert "httpx.Client(\n            trust_env=False, timeout=timeout)" in src, (
        "内网直连 http client 必须显式继承 timeout（默认近乎无限）")
