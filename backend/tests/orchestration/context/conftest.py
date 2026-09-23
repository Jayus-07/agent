"""STOP G 测试隔离：ConversationContext repository 换进程内 Memory 实现。

生产默认 backend=redis（宿主机 .env REDIS_ENABLED=true 会连真 Redis）。
单元测试只 mock 外部边界（Redis），autouse 把进程单例替换为全新
MemoryConversationContextRepository——测试互不串扰、零真实 Redis 写入。
多 worker / 真 Redis 行为由 tests/test_stop_g4_matrix.py 显式实测（不走本 fixture）。
"""
from __future__ import annotations

import pytest

import backend.orchestration.context.context_repository as repo_mod
from backend.orchestration.context.context_repository import (
    MemoryConversationContextRepository,
)


@pytest.fixture(autouse=True)
def _memory_context_repo(monkeypatch):
    repo = MemoryConversationContextRepository(ttl_seconds=1800, max_entries=100)
    monkeypatch.setattr(repo_mod, "_repo", repo)
    yield repo
    monkeypatch.setattr(repo_mod, "_repo", None)
