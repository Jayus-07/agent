"""typing_state 单元测试 — 双向「输入中」瞬态状态（2026-09-18）。

覆盖：Redis 路径（setex/exists 语义）、Redis 不可用降级进程内存、
TTL 过期自然失效。不依赖真实 Redis（get_redis 桩掉）。
"""
from __future__ import annotations

import pytest

from backend.customer_service import typing_state


@pytest.fixture(autouse=True)
def _clean_local():
    """每用例清空降级存储，避免用例间串扰。"""
    typing_state._local_state.clear()
    yield
    typing_state._local_state.clear()


class _FakeRedis:
    """最小 sync redis 桩：记录 setex / 模拟 exists。"""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.setex_calls: list[tuple[str, int, str]] = []

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.setex_calls.append((key, ttl, value))
        self.store[key] = value

    def exists(self, key: str) -> int:
        return 1 if key in self.store else 0


def _use_redis(monkeypatch, r):
    monkeypatch.setattr(
        "backend.infra.redis.client.get_redis", lambda: r
    )


def test_redis_path_set_and_check(monkeypatch):
    """Redis 可用：SETEX 写 TTL，exists 读命中。"""
    r = _FakeRedis()
    _use_redis(monkeypatch, r)

    typing_state.set_typing("agent", "conv-1")
    assert r.setex_calls == [("cs:typing:agent:conv-1", 5, "1")]
    assert typing_state.is_typing("agent", "conv-1") is True
    assert typing_state.is_typing("agent", "conv-2") is False


def test_redis_unavailable_falls_back_to_local(monkeypatch):
    """Redis 不可用（返回 None）：降级进程内存，语义等价。"""
    _use_redis(monkeypatch, None)

    assert typing_state.is_typing("user", "conv-1") is False
    typing_state.set_typing("user", "conv-1")
    assert typing_state.is_typing("user", "conv-1") is True
    assert typing_state.is_typing("agent", "conv-1") is False  # side 隔离


def test_redis_error_falls_back_to_local(monkeypatch):
    """get_redis 抛异常：同样降级进程内存，不向外传播。"""
    def _boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(
        "backend.infra.redis.client.get_redis", _boom
    )

    typing_state.set_typing("agent", "conv-1")
    assert typing_state.is_typing("agent", "conv-1") is True


def test_local_ttl_expiry(monkeypatch):
    """降级路径 TTL 过期：超过窗口后 is_typing=False 并清理条目。"""
    _use_redis(monkeypatch, None)
    typing_state.set_typing("agent", "conv-1")

    # 人为把过期时刻拨到过去（等价 TTL 5s 流逝）
    key = "cs:typing:agent:conv-1"
    typing_state._local_state[key] = 0.0
    assert typing_state.is_typing("agent", "conv-1") is False
    assert key not in typing_state._local_state


def test_ttl_constant():
    """TTL 5s：与前端提示展示窗口（4s）衔接，服务端略长保证可见。"""
    assert typing_state.TYPING_TTL_SECONDS == 5
