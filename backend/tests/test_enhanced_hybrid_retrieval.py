"""enhanced_hybrid_retrieval 回归测试（P0-3）。

背景：该模块因导入不存在的 MULTI_QUERY_ENABLED 长期 ImportError，
hybrid_retrieve 每次调用都静默回退原始路径；且 _ultimate_rrf_fusion
只认 chunk_type 标记，真实召回（无该标记）会被全部丢弃返回空。
"""
from langchain_core.documents import Document


class TestEnhancedModuleImport:
    def test_module_imports_cleanly(self):
        """模块可正常导入（历史缺陷：MULTI_QUERY_ENABLED 不存在导致 ImportError）。"""
        import importlib

        mod = importlib.import_module(
            "backend.rag.retrieval.enhanced_hybrid_retrieval"
        )
        assert hasattr(mod, "enhanced_hybrid_retrieve")
        assert hasattr(mod, "ConfidenceAggregator")


class TestUltimateRRFFusion:
    def _docs(self):
        return [
            Document(page_content="A", metadata={"chunk_id": "a1"}),
            Document(page_content="B", metadata={"chunk_id": "b2"}),
            Document(page_content="C", metadata={"chunk_id": "c3"}),
        ]

    def test_fusion_keeps_docs_without_chunk_type_marker(self):
        """无 chunk_type 标记的真实召回不得被丢弃（此前静默返回空列表）。"""
        from backend.rag.retrieval.enhanced_hybrid_retrieval import (
            _ultimate_rrf_fusion,
        )

        merged = _ultimate_rrf_fusion([(self._docs(), 1.0)], rrf_k=60, top_k=5)
        assert len(merged) == 3
        assert {d.metadata["chunk_id"] for d in merged} == {"a1", "b2", "c3"}

    def test_fusion_dedups_by_chunk_id(self):
        """三路召回中同一 chunk 重复出现 → 融合后只保留一份。"""
        from backend.rag.retrieval.enhanced_hybrid_retrieval import (
            _ultimate_rrf_fusion,
        )

        docs = self._docs() + self._docs()[:2]  # a1/b2 重复
        merged = _ultimate_rrf_fusion([(docs, 1.0)], rrf_k=60, top_k=5)
        cids = [d.metadata["chunk_id"] for d in merged]
        assert len(cids) == len(set(cids))
        assert set(cids) == {"a1", "b2", "c3"}

    def test_fusion_respects_top_k(self):
        from backend.rag.retrieval.enhanced_hybrid_retrieval import (
            _ultimate_rrf_fusion,
        )

        merged = _ultimate_rrf_fusion([(self._docs(), 1.0)], rrf_k=60, top_k=2)
        assert len(merged) == 2

    def test_fusion_empty_input(self):
        from backend.rag.retrieval.enhanced_hybrid_retrieval import (
            _ultimate_rrf_fusion,
        )

        assert _ultimate_rrf_fusion([], rrf_k=60, top_k=5) == []


def test_parallel_retrieval_branch_timing_preserves_results():
    from backend.rag.retrieval.enhanced_hybrid_retrieval import _timed_retrieval

    timings = {}
    result = _timed_retrieval(timings, "dense", lambda value: [value], "candidate")

    assert result == ["candidate"]
    assert timings["dense"] >= 0


def test_shared_deadline_keeps_ready_sparse_without_waiting_for_late_dense(monkeypatch):
    """迟到向量不阻塞已完成稀疏证据，也不能在收口后改结果。"""
    from concurrent.futures import Future
    from unittest.mock import Mock
    from backend.rag.retrieval.enhanced_hybrid_retrieval import enhanced_hybrid_retrieve
    from backend.rag.retrieval_budget import scoped_retrieval_budget

    late = Future()
    late.set_running_or_notify_cancel()
    ready = Future()
    doc = Document(page_content="证据", metadata={"chunk_id": "s", "doc_id": "d"})
    ready.set_result([doc])
    monkeypatch.setattr("backend.infra.thread_pools.submit_rag_task",
                        Mock(side_effect=[late, ready]))
    with scoped_retrieval_budget(10):
        docs, meta = enhanced_hybrid_retrieve("采购流程", Mock(), Mock())
    assert docs == [doc]
    assert meta["metrics"]["dense_status"] == "timeout"
    assert not meta["metrics"]["fallback_used"]
    late.set_result([Document(page_content="迟到", metadata={"chunk_id": "late"})])
    assert docs == [doc]


def test_all_paths_expired_do_not_restart_fallback(monkeypatch):
    """耗尽预算不得再发起一轮模型召回。"""
    from concurrent.futures import Future
    from unittest.mock import Mock
    import pytest
    from backend.rag.retrieval.enhanced_hybrid_retrieval import enhanced_hybrid_retrieve
    from backend.rag.retrieval_budget import (
        RetrievalDeadlineExceeded, scoped_retrieval_budget,
    )

    futures = [Future(), Future()]
    monkeypatch.setattr("backend.infra.thread_pools.submit_rag_task",
                        Mock(side_effect=futures))
    fallback = Mock()
    monkeypatch.setattr("backend.rag.retrieval.hybrid.hybrid_retrieve", fallback)
    with scoped_retrieval_budget(0), pytest.raises(RetrievalDeadlineExceeded):
        enhanced_hybrid_retrieve("采购流程", Mock(), Mock())
    assert all(f.cancelled() for f in futures)
    fallback.assert_not_called()


def test_future_without_budget_preserves_direct_call_contract():
    from concurrent.futures import Future
    from backend.rag.retrieval_budget import await_retrieval

    future = Future()
    future.set_result(["结果"])
    assert await_retrieval(future) == ["结果"]


def test_provider_timeout_preserves_original_error():
    from concurrent.futures import Future
    import pytest
    from backend.rag.retrieval_budget import await_retrieval, scoped_retrieval_budget

    future = Future()
    error = TimeoutError("Provider 自身超时")
    future.set_exception(error)
    with scoped_retrieval_budget(1000), pytest.raises(TimeoutError) as caught:
        await_retrieval(future)
    assert caught.value is error


def test_real_worker_cannot_extend_callers_deadline():
    """实际线程迟到返回时，调用方已经收口，Worker仍可完成结算。"""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import pytest
    from backend.rag.retrieval_budget import (
        RetrievalDeadlineExceeded, await_retrieval, scoped_retrieval_budget,
    )

    released = Event()
    started = Event()

    def worker():
        started.set()
        assert released.wait(2)
        return "迟到 usage 已结算"

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker)
        assert started.wait(1)
        try:
            with scoped_retrieval_budget(5), pytest.raises(RetrievalDeadlineExceeded):
                await_retrieval(future)
            assert not future.done()
        finally:
            released.set()
        assert future.result(timeout=1) == "迟到 usage 已结算"
