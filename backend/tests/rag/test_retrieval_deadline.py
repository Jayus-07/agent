"""检索级 deadline 预算回归测试（Phase 5，spec §23-§25）。

铁律：预算耗尽 ≠ 异常——各阶段阶梯降级（rewrite→原始 query、
fanout→部分成功、adaptive/synonym→跳过），绝不整链失败。
"""
from __future__ import annotations

import time

import pytest
from langchain_core.documents import Document
from types import SimpleNamespace


@pytest.fixture(autouse=True)
def _clean_budget():
    from backend.rag.retrieval_budget import clear_retrieval_budget

    yield
    clear_retrieval_budget()


def _set_expired_budget(total_ms: float = 8000) -> None:
    """直接放置一个已过期的预算（模拟检索阶段已耗尽）。"""
    from backend.rag.retrieval_budget import _budget_var, RetrievalBudget

    _budget_var.set(
        RetrievalBudget(total_ms=total_ms, deadline_at=time.monotonic() - 1),
    )


# ── 预算模型 ─────────────────────────────────────────────────────────────


def test_budget_remaining_and_expiry():
    from backend.rag.retrieval_budget import RetrievalBudget

    b = RetrievalBudget(total_ms=200)
    assert 0 < b.remaining_ms() <= 210  # 时钟精度容忍 1ms 级浮点误差
    assert not b.expired()
    expired = RetrievalBudget(total_ms=100, deadline_at=time.monotonic() - 1)
    assert expired.expired()
    assert expired.remaining_ms() < 0


def test_remaining_ms_without_budget_returns_default():
    from backend.rag.retrieval_budget import remaining_ms

    assert remaining_ms(1234) == 1234


def test_budget_slice_capped_by_remaining():
    from backend.rag.retrieval_budget import RetrievalBudget

    rich = RetrievalBudget(total_ms=5000)
    assert rich.slice(1000).total_ms == 1000
    poor = RetrievalBudget(total_ms=100, deadline_at=time.monotonic() - 0.05)
    assert poor.slice(1000).total_ms <= 60


# ── MQ rewrite 降级（§25：超时/预算不足 → 原始 query）───────────────────


def _mq_retriever(monkeypatch, variants: list[str], invoke):
    import backend.rag.retrieval.multi_query as mq
    from types import SimpleNamespace as _NS

    monkeypatch.setattr(mq, "need_multi_query", lambda q: (True, "test-forced"))
    monkeypatch.setattr(mq, "_rewrite", lambda q: variants)
    monkeypatch.setattr(
        mq, "get_context",
        lambda: _NS(mq_triggered=False, mq_reason="", mq_variants=1, mq_filtered=1),
    )
    return mq.MultiQueryRetriever(base_retriever=type("R", (), {"invoke": staticmethod(invoke)})())


def test_rewrite_skipped_when_budget_exhausted(monkeypatch):
    """预算耗尽：_rewrite 不得被调用，直接原始 query 走 hybrid。"""
    import backend.rag.retrieval.multi_query as mq

    _set_expired_budget()

    def _no_rewrite(q):
        raise AssertionError("预算耗尽时不得调用 LLM 改写")

    def _invoke(q):
        return [Document(page_content="d", metadata={"chunk_id": "c1"})]

    monkeypatch.setattr(mq, "_rewrite", _no_rewrite)
    retriever = _mq_retriever(monkeypatch, ["x"], _invoke)  # variants 不会被用到
    monkeypatch.setattr(mq, "need_multi_query", lambda q: (True, "t"))
    docs = retriever.invoke("测试问题")
    assert len(docs) == 1


def test_fanout_partial_success_on_timeout(monkeypatch):
    """§25：Q1/Q2 完成、Q3 慢 → 用部分结果，不抛异常，留 _mq_partial。"""
    from backend.config import rag as rag_config

    monkeypatch.setattr(rag_config, "RAG_MULTI_QUERY_FANOUT_TIMEOUT_MS", 400)
    # 预算充足（fanout 取 min(配置, 剩余)）
    from backend.rag.retrieval_budget import start_retrieval_budget

    start_retrieval_budget(8000)

    def _invoke(q: str) -> list[Document]:
        if "慢" in q:
            time.sleep(5)  # 模拟挂起的变体
        return [Document(page_content=f"d-{q}", metadata={"chunk_id": f"c-{q}"})]

    retriever = _mq_retriever(
        monkeypatch,
        ["原始问题", "变体乙", "慢变体丙"],
        _invoke,
    )
    t0 = time.monotonic()
    docs = retriever.invoke("原始问题")
    elapsed = time.monotonic() - t0

    assert elapsed < 3, f"fan-out 超时未生效，耗时 {elapsed:.1f}s"
    sources = {d.metadata["chunk_id"] for d in docs}
    assert "c-原始问题" in sources and "c-变体乙" in sources
    assert "c-慢变体丙" not in sources
    assert any(d.metadata.get("_mq_partial") for d in docs)


# ── adaptive / synonym 预算守卫（§25：无预算 → 不扩/不重试）────────────


def test_adaptive_expand_skips_when_budget_exhausted(monkeypatch):
    from backend.rag.retrieval import retrievers as ret_mod
    from backend.rag.retrieval.retrievers import ChunkLevelRetriever

    _set_expired_budget()

    calls = {"n": 0}

    def _no_hybrid(*a, **k):
        calls["n"] += 1
        return []

    monkeypatch.setattr(ret_mod, "hybrid_retrieve", _no_hybrid)
    from backend import config as config_pkg

    monkeypatch.setattr(config_pkg, "ADAPTIVE_RETRIEVAL_ENABLED", True)
    monkeypatch.setattr(config_pkg, "ADAPTIVE_MIN_CHUNKS", 999)
    monkeypatch.setattr(config_pkg, "ADAPTIVE_K_STEPS", [8, 12, 16])

    # pydantic v2：model_construct 绕过校验构造测试实例
    r = ChunkLevelRetriever.model_construct(k=8, chunk_retriever=None, bm25=None)

    docs = [Document(page_content="短", metadata={"doc_id": "d1"})]
    out, effective_k = r._adaptive_expand("q", docs, None, None, set())
    assert calls["n"] == 0, "预算耗尽时不得重跑 hybrid 扩展"
    assert out is docs and effective_k == 8


def test_stage2_synonym_retry_skipped_when_budget_exhausted(monkeypatch):
    """空召回 + 预算耗尽 → 不做同义词重试（_hybrid_collect 只跑首查一次）。"""
    from types import SimpleNamespace

    from backend.rag.retrieval import retrievers as ret_mod
    from backend.rag.retrieval.retrievers import ChunkLevelRetriever

    _set_expired_budget()

    calls = {"n": 0}

    def _fake_collect(self, st, expanded):
        calls["n"] += 1

    monkeypatch.setattr(ChunkLevelRetriever, "_hybrid_collect", _fake_collect)
    import backend.rag.preprocessing.synonyms as syn_mod

    monkeypatch.setattr(syn_mod, "expand_query", lambda q: [q, "同义变体"])

    r = ChunkLevelRetriever.model_construct(k=8)
    st = SimpleNamespace(
        docs=[], expanded_queries=None, query="口语化问题", doc_ids=None,
        span=None, stage1_path="s1", stage1_fallback_count=0,
    )
    r._stage2_hybrid_retrieve(st)
    assert calls["n"] == 1, "预算耗尽时同义词重试不得触发第二次 hybrid"


def test_stage2_synonym_retry_runs_without_budget(monkeypatch):
    """无预算（旁路调用）→ 守卫放行，重试语义与旧版一致。"""
    from types import SimpleNamespace

    from backend.rag.retrieval.retrievers import ChunkLevelRetriever

    calls = {"n": 0}

    def _fake_collect(self, st, expanded):
        calls["n"] += 1

    monkeypatch.setattr(ChunkLevelRetriever, "_hybrid_collect", _fake_collect)
    import backend.rag.preprocessing.synonyms as syn_mod

    monkeypatch.setattr(syn_mod, "expand_query", lambda q: [q, "同义变体"])

    r = ChunkLevelRetriever.model_construct(k=8)
    st = SimpleNamespace(
        docs=[], expanded_queries=None, query="口语化问题", doc_ids=None,
        # add_event 需要 span.events 容器
        span=SimpleNamespace(events=[]), stage1_path="s1", stage1_fallback_count=0,
    )
    r._stage2_hybrid_retrieve(st)
    assert calls["n"] == 2, "无预算时空召回重试应照常执行（首查+同义词重试）"


def test_adaptive_and_synonym_keep_default_behavior_without_budget(monkeypatch):
    """无预算（旁路调用）：remaining_ms 返回门槛值 → 行为与旧版一致。"""
    from backend.config.rag import (
        RAG_ADAPTIVE_MIN_BUDGET_MS,
        RAG_SYNONYM_RETRY_MIN_BUDGET_MS,
    )
    from backend.rag.retrieval_budget import remaining_ms

    # 无预算（fixture 已清理）→ 恒返回门槛值本身 → 守卫恒放行
    assert remaining_ms(RAG_ADAPTIVE_MIN_BUDGET_MS) == RAG_ADAPTIVE_MIN_BUDGET_MS
    assert remaining_ms(RAG_SYNONYM_RETRY_MIN_BUDGET_MS) == RAG_SYNONYM_RETRY_MIN_BUDGET_MS
