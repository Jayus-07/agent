"""tests/orchestration/router/test_vector_router_rebuild_throttle.py — 路由索引重建节流

2026-10-28 事故回归：MCP 动态工具快照抖动（116↔118 每 ~30s 翻转）触发
连续全量重建，每次 ~116 条串行 embedding 独占共享推理槽数秒，用户请求
的路由 embedding 被排队（实测领域判断 5.4s、普通改写 8.7s）。

契约：
  1. 冷却期内的条数不符沿用现有索引，不重建；
  2. 内容签名一致（同量/并发读数漂移）不重建；
  3. 冷却过期且内容确变 → 重建一次并登记新签名（幂等，双检防并发重复）；
  4. 空索引首建不受冷却限制。
"""
from __future__ import annotations

import threading
import time

import pytest


@pytest.fixture()
def fake_store_cls(monkeypatch):
    """替换 pgvector 存储与 embedding 获取；count 用类属性预置。"""
    made = {"added": []}

    class FakeStore:
        count_value = 0

        def __init__(self, persist_directory=None, embedding_function=None):
            made["instance"] = self

        def count(self):
            return self.count_value

        def add_texts(self, texts, metadatas):
            made["added"].extend(texts)

    import backend.rag.embedding_singleton as emb_mod
    import backend.rag.vectorstore.pgvector_store as store_mod

    monkeypatch.setattr(emb_mod, "get_embedding", lambda: object())
    monkeypatch.setattr(store_mod, "PgVectorKnowledgeStore", FakeStore)
    made["cls"] = FakeStore
    return made


def _router():
    from backend.orchestration.router.vector_router import VectorRouter

    r = object.__new__(VectorRouter)
    r.collection_name = "test-router-index"
    r._rebuild_lock = threading.RLock()
    r._last_rebuild_at = 0.0
    r._last_built_signature = None
    return r


def test_mismatch_within_cooldown_skips_rebuild(fake_store_cls, monkeypatch):
    # 与 expected 不符 → 对账分支（expected 取当前 manifest 实际值）
    from backend.orchestration.router.vector_router import (
        _expected_example_count,
    )
    fake_store_cls["cls"].count_value = _expected_example_count() + 1
    router = _router()
    router._last_rebuild_at = time.monotonic()
    router._last_built_signature = "stale-signature"
    rebuild_calls = []
    monkeypatch.setattr(
        router, "_rebuild_index", lambda: rebuild_calls.append(1))

    router._ensure_index()

    assert rebuild_calls == []
    assert router._store is not None  # 现有索引未被清空，继续可用


def test_mismatch_after_cooldown_rebuilds_and_records_signature(
        fake_store_cls, monkeypatch):
    from backend.orchestration.router.vector_router import _expected_signature

    from backend.orchestration.router.vector_router import (
        _expected_example_count,
    )
    fake_store_cls["cls"].count_value = _expected_example_count() + 1
    router = _router()
    router._last_rebuild_at = time.monotonic() - 400.0
    router._last_built_signature = "stale-signature"
    rebuild_calls = []
    monkeypatch.setattr(
        router, "_rebuild_index", lambda: rebuild_calls.append(1))

    router._ensure_index()

    assert rebuild_calls == [1]
    assert router._last_built_signature == _expected_signature()
    assert router._last_rebuild_at > time.monotonic() - 5.0


def test_signature_unchanged_skips_rebuild_even_after_cooldown(
        fake_store_cls, monkeypatch):
    from backend.orchestration.router.vector_router import _expected_signature

    from backend.orchestration.router.vector_router import (
        _expected_example_count,
    )
    fake_store_cls["cls"].count_value = _expected_example_count() + 1
    router = _router()
    router._last_rebuild_at = time.monotonic() - 400.0
    router._last_built_signature = _expected_signature()
    rebuild_calls = []
    monkeypatch.setattr(
        router, "_rebuild_index", lambda: rebuild_calls.append(1))

    router._ensure_index()

    assert rebuild_calls == []


def test_second_reconcile_same_signature_is_idempotent(fake_store_cls, monkeypatch):
    """连续两次对账（冷却均过期）：第一次重建后，第二次因签名一致跳过。"""
    from backend.orchestration.router.vector_router import _expected_signature

    from backend.orchestration.router.vector_router import (
        _expected_example_count,
    )
    fake_store_cls["cls"].count_value = _expected_example_count() + 1
    router = _router()
    router._last_rebuild_at = time.monotonic() - 400.0
    router._last_built_signature = "stale-signature"
    rebuild_calls = []
    monkeypatch.setattr(
        router, "_rebuild_index", lambda: rebuild_calls.append(1))

    router._ensure_index()
    router._last_rebuild_at = time.monotonic() - 400.0  # 再次让冷却过期
    router._ensure_index()

    assert rebuild_calls == [1]
    assert router._last_built_signature == _expected_signature()


def test_empty_index_first_build_ignores_cooldown(fake_store_cls, monkeypatch):
    from backend.orchestration.router.vector_router import _expected_signature

    fake_store_cls["cls"].count_value = 0
    router = _router()
    router._last_rebuild_at = time.monotonic()  # 刚重建过也不能阻止首建
    router._last_built_signature = _expected_signature()

    router._ensure_index()

    assert len(fake_store_cls["added"]) > 0
