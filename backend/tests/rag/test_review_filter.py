"""4.1b 检索层软过滤 — pending_review 文档不被 hybrid_retrieve 返回。

契约：
- hybrid_retrieve 结果剔除 doc_id ∈ pending_review 集合的 chunk
- 集合 60s 进程内缓存；registry 不可用时跳过过滤（可用性优先）
- 无待审文档 → 原样返回（零开销）
"""
import pytest
from langchain_core.documents import Document

import backend.rag.retrieval.hybrid as hybrid_mod
from backend.rag.retrieval.hybrid import (
    _filter_review_blocked,
    _pending_review_doc_ids,
    hybrid_retrieve,
)


def _doc(doc_id: str, content: str = "正文") -> Document:
    return Document(page_content=content, metadata={"doc_id": doc_id})


@pytest.fixture(autouse=True)
def _reset_cache():
    hybrid_mod._review_block_cache = {"ids": frozenset(), "ts": 0.0}
    yield
    hybrid_mod._review_block_cache = {"ids": frozenset(), "ts": 0.0}


class TestPendingReviewIds:

    def test_reads_registry(self, monkeypatch):
        class FakeRegistry:
            def list_by_statuses(self, statuses):
                assert statuses == ("pending_review",)
                return [{"doc_id": "d1"}, {"doc_id": "d2"}, {"doc_id": ""}]

        from backend.rag.indexing import doc_registry as dr
        monkeypatch.setattr(dr, "DocumentRegistry", lambda path: FakeRegistry())
        ids = _pending_review_doc_ids()
        assert ids == frozenset({"d1", "d2"})  # 空 doc_id 被剔除

    def test_cache_prevents_repeat_query(self, monkeypatch):
        calls = {"n": 0}

        class FakeRegistry:
            def list_by_statuses(self, statuses):
                calls["n"] += 1
                return [{"doc_id": "d1"}]

        from backend.rag.indexing import doc_registry as dr
        monkeypatch.setattr(dr, "DocumentRegistry", lambda path: FakeRegistry())
        _pending_review_doc_ids()
        _pending_review_doc_ids()
        assert calls["n"] == 1, "60s 缓存内不重复查库"


class TestFilterReviewBlocked:

    def test_blocks_pending_review_docs(self, monkeypatch):
        monkeypatch.setattr(hybrid_mod, "_pending_review_doc_ids",
                            lambda: frozenset({"blocked"}))
        docs = [_doc("ok1"), _doc("blocked"), _doc("ok2")]
        out = _filter_review_blocked(docs)
        assert [d.metadata["doc_id"] for d in out] == ["ok1", "ok2"]

    def test_empty_blocked_set_noop(self, monkeypatch):
        monkeypatch.setattr(hybrid_mod, "_pending_review_doc_ids",
                            lambda: frozenset())
        docs = [_doc("a"), _doc("b")]
        out = _filter_review_blocked(docs)
        assert out is docs, "无待审文档时原样返回（零开销路径）"

    def test_docs_without_metadata_dropped_if_blocked_unknown(self, monkeypatch):
        monkeypatch.setattr(hybrid_mod, "_pending_review_doc_ids",
                            lambda: frozenset({"x"}))
        plain = "纯字符串"
        out = _filter_review_blocked([plain])
        assert out == [plain], "无 metadata 的对象按不属于 blocked 处理"


class TestHybridRetrieveWrapper:

    def test_wrapper_filters_impl_result(self, monkeypatch):
        """包装层对 _hybrid_retrieve_impl 的全部出口生效。"""
        monkeypatch.setattr(hybrid_mod, "_pending_review_doc_ids",
                            lambda: frozenset({"bad"}))

        def fake_impl(query, vr, br, k=5, doc_ids=None, rrf_k=60,
                      metadata_filter=None, expanded_queries=None):
            return [_doc("good"), _doc("bad")]

        monkeypatch.setattr(hybrid_mod, "_hybrid_retrieve_impl", fake_impl)
        out = hybrid_retrieve("q", None, None)
        assert [d.metadata["doc_id"] for d in out] == ["good"]
