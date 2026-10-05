"""tests/api/test_budget_soft_overflow.py — D-9 优雅降级单元测试

压缩后仍超预算（overflow 终局）→ 保留 system+最新消息、尾部截断，
不再直接抛 ContextBudgetExceededError（500）；env 可回退硬拒。
"""
from __future__ import annotations

import pytest

from backend.infra.llm import proxy as proxy_mod


class _Msg:
    def __init__(self, content, role="human"):
        self.content = content
        self.role = role

    def __class_getitem__(cls, item):
        return cls


class _FakeMessage:
    """足够像 BaseMessage 的对象（count_message_tokens mock 不依赖类型）。"""

    def __init__(self, content):
        self.content = content
        self.additional_kwargs = {}


@pytest.fixture
def patched(monkeypatch):
    """免依赖桩：token 计数按 1 字符=1 token 的简化口径。"""
    import backend.memory.token_budget as tb

    monkeypatch.setattr(tb, "count_message_tokens",
                        lambda m: len(str(m.content)))
    monkeypatch.setattr(proxy_mod, "_soft_overflow_enabled", lambda: True)
    return tb


def _overflow_branch(payload, budget, monkeypatch):
    """直接驱动 _truncate_messages_to_budget（overflow 分支的降级函数）。"""
    return proxy_mod._truncate_messages_to_budget(payload, budget)


def test_truncate_keeps_head_and_tail(patched):
    system = _FakeMessage("你是助手" * 10, role="system")
    middles = [_FakeMessage("中间历史" * 50) for _ in range(5)]
    tail = _FakeMessage("最新问题" + "证据" * 5000)
    payload = [system] + middles + [tail]
    out = _overflow_branch(payload, budget=1200, monkeypatch=patched)
    assert len(out) == 2  # system + 截断后的最新消息
    assert out[0] is system
    assert "已截断尾部" in out[-1].content


def test_short_tail_not_truncated(patched):
    system = _FakeMessage("sys", role="system")
    tail = _FakeMessage("短问题")
    out = _overflow_branch([system, tail], budget=10000, monkeypatch=patched)
    assert out[-1].content == "短问题"


def test_soft_overflow_toggle(monkeypatch):
    monkeypatch.setenv("CONTEXT_BUDGET_SOFT_OVERFLOW", "false")
    assert proxy_mod._soft_overflow_enabled() is False
    monkeypatch.setenv("CONTEXT_BUDGET_SOFT_OVERFLOW", "true")
    assert proxy_mod._soft_overflow_enabled() is True
