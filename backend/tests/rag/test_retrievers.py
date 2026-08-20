"""RAG Retriever 核心链路测试（P1 整改新增）。

覆盖任务要求的 Retrieval fallback 可观测性与『系统失败不伪装成没有资料』：
  1. Vector 检索失败 → 异常向上传播（不静默返回空 context 伪装成无资料）
  2. BM25 无结果 → 纯 Vector 结果正常返回（Hybrid 融合不丢一侧）
  3. Stage 2 全空 → Neighbor Expansion fallback（有日志留痕）
  4. parent_lookup 失败 → 保留原检索结果（有日志留痕）
  5. AdaptiveRetriever Cluster 检测 → Context Expansion
"""
import pytest
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from types import SimpleNamespace


# =====================================================
# Hybrid 检索 fallback 行为
# =====================================================

class TestHybridFallback:
    def test_vector_failure_falls_back_to_bm25(self):
        """Vector 崩溃 → 降级仅用 BM25，结果正常返回且 span 标记 fallback（可观测）。"""
        from backend.observability.tracer import trace_collector
        from backend.rag.retrieval.hybrid import hybrid_retrieve

        # mock 签名须与 CustomRetriever.retrieve 对齐:
        # 2026-08-20 hybrid_retrieve 新增 expanded_queries 透传,缺参会 TypeError
        def boom(q, k=5, doc_ids=None, metadata_filter=None, expanded_queries=None):
            raise RuntimeError("vector db down")
        v = SimpleNamespace(retrieve=boom)
        b = SimpleNamespace(invoke=lambda q: [
            Document(page_content="b1", metadata={"chunk_id": "b1", "doc_id": "d1"}),
        ])

        trace = trace_collector.start("hybrid-fallback", session_id="t1")
        try:
            trace_collector.start_span("root", parent_id=None, name="test", type="agent")
        except RuntimeError:
            pass  # root 已存在

        merged = hybrid_retrieve("q", v, b, k=5)
        assert len(merged) == 1
        assert merged[0].metadata["chunk_id"] == "b1"

        # fallback 可观测：retrieval span 的 metrics 带 fallback_side
        ret_span = next(
            (s for s in trace.spans if s.span_id == "hybrid_retrieval"), None
        )
        assert ret_span is not None
        assert ret_span.metrics.get("fallback_side") == "vector"
        assert "vector db down" in ret_span.metrics.get("fallback_reason", "")

    def test_both_fail_propagates(self):
        """两侧都失败 → 异常向上传播（真系统失败，不伪装成『没有资料』）。"""
        from backend.rag.retrieval.hybrid import hybrid_retrieve

        def boom_v(q, k=5, doc_ids=None, metadata_filter=None, expanded_queries=None):
            raise RuntimeError("vector db down")
        def boom_b(q):
            raise RuntimeError("bm25 db down")
        v = SimpleNamespace(retrieve=boom_v)
        b = SimpleNamespace(invoke=boom_b)
        with pytest.raises(RuntimeError, match="均失败"):
            hybrid_retrieve("q", v, b, k=5)

    def test_vector_only_when_bm25_empty(self):
        """BM25 无结果 → 保留 Vector 结果（Hybrid 融合不丢一侧）。"""
        from backend.rag.retrieval.hybrid import hybrid_retrieve
        v = SimpleNamespace(retrieve=lambda q, k=5, doc_ids=None, metadata_filter=None, expanded_queries=None: [
            Document(page_content="v1", metadata={"chunk_id": "v1", "doc_id": "d1"}),
        ])
        b = SimpleNamespace(invoke=lambda q: [])
        merged = hybrid_retrieve("q", v, b, k=5)
        assert len(merged) == 1
        assert merged[0].metadata["chunk_id"] == "v1"

    def test_bm25_only_when_vector_empty(self):
        """Vector 无结果 → 保留 BM25 结果。"""
        from backend.rag.retrieval.hybrid import hybrid_retrieve
        v = SimpleNamespace(retrieve=lambda q, k=5, doc_ids=None, metadata_filter=None, expanded_queries=None: [])
        b = SimpleNamespace(invoke=lambda q: [
            Document(page_content="b1", metadata={"chunk_id": "b1", "doc_id": "d1"}),
        ])
        merged = hybrid_retrieve("q", v, b, k=5)
        assert len(merged) == 1
        assert merged[0].metadata["chunk_id"] == "b1"

    def test_both_empty_returns_empty(self):
        """两侧都无结果 → 返回空列表（由上层 Gate 处理 NO_EVIDENCE 拒答）。"""
        from backend.rag.retrieval.hybrid import hybrid_retrieve
        v = SimpleNamespace(retrieve=lambda q, k=5, doc_ids=None, metadata_filter=None, expanded_queries=None: [])
        b = SimpleNamespace(invoke=lambda q: [])
        merged = hybrid_retrieve("q", v, b, k=5)
        assert merged == []


# =====================================================
# ChunkLevelRetriever 降级链
# =====================================================

def _make_retriever(doc_db, chunk_retriever, bm25):
    from backend.rag.retrieval.retrievers import ChunkLevelRetriever
    return ChunkLevelRetriever(
        doc_db=doc_db, vectordb=None,
        chunk_retriever=chunk_retriever, bm25=bm25, person_index={},
    )


class TestChunkLevelFallback:
    def test_stage2_empty_falls_back_to_neighbor_expansion(self):
        """Stage 2 无结果 → Neighbor Expansion 用 doc 级检索拉全文（fallback 可观测）。"""
        doc_db = SimpleNamespace(
            similarity_search=lambda q, k=5, filter=None: [
                Document(page_content="doc", metadata={"doc_id": "d1"}),
            ],
            get=lambda where: {
                "documents": ["全文内容一", "全文内容二"],
                "metadatas": [
                    {"doc_id": "d1", "chunk_id": "d1c1"},
                    {"doc_id": "d1", "chunk_id": "d1c2"},
                ],
            },
        )
        chunk_retriever = SimpleNamespace(
            retrieve=lambda q, k=5, doc_ids=None, metadata_filter=None, expanded_queries=None: [],
        )
        bm25 = SimpleNamespace(invoke=lambda q: [])
        r = _make_retriever(doc_db, chunk_retriever, bm25)
        r.k = 5

        docs = r._get_relevant_documents("查不到的问题")
        assert len(docs) == 2
        assert {d.metadata["chunk_id"] for d in docs} == {"d1c1", "d1c2"}

    def test_parent_lookup_failure_keeps_original_docs(self):
        """parent_lookup 抛异常 → 返回原检索结果（降级不丢结果，有日志留痕）。"""
        from backend.rag.retrieval.retrievers import attach_parent_context
        leaf = Document(page_content="leaf", metadata={
            "chunk_id": "l1", "granularity": "leaf", "parent_chunk_id": "p1",
        })

        def boom(ids):
            raise RuntimeError("db down")
        result = attach_parent_context([leaf], boom)
        assert len(result) == 1
        assert result[0].metadata["chunk_id"] == "l1"


# =====================================================
# AdaptiveRetriever Context Expansion
# =====================================================

class _FakeBaseRetriever(BaseRetriever):
    """最小 BaseRetriever 实现（pydantic 校验要求真实子类）。"""

    docs: list = []

    def _get_relevant_documents(self, query: str, *, run_manager=None):
        return list(self.docs)


class TestAdaptiveRetriever:
    def test_cluster_triggers_context_expansion(self):
        """命中集中在少数文档 → Context Expansion 拉全文。"""
        from backend.rag.retrieval.retrievers import AdaptiveRetriever
        base = _FakeBaseRetriever(docs=[
            Document(page_content="c1", metadata={"doc_id": "d1"}),
            Document(page_content="c2", metadata={"doc_id": "d1"}),
        ])
        doc_db = SimpleNamespace(get=lambda where: {
            "documents": ["文档全文"],
            "metadatas": [{"doc_id": "d1"}],
        })
        ar = AdaptiveRetriever(base_retriever=base, doc_db=doc_db)
        docs = ar._get_relevant_documents("q")
        # 1 全文 + 2 chunks
        assert len(docs) == 3
        assert docs[0].page_content == "文档全文"

    def test_dispersed_keeps_chunks_only(self):
        """命中分散在多个文档 → 跳过 Expansion（避免上下文污染）。"""
        from backend.rag.retrieval.retrievers import AdaptiveRetriever
        base = _FakeBaseRetriever(docs=[
            Document(page_content="c1", metadata={"doc_id": "d1"}),
            Document(page_content="c2", metadata={"doc_id": "d2"}),
        ])
        doc_db = SimpleNamespace(get=lambda where: {
            "documents": [], "metadatas": [],
        })
        ar = AdaptiveRetriever(base_retriever=base, doc_db=doc_db)
        docs = ar._get_relevant_documents("q")
        assert len(docs) == 2
        assert all("全文" not in d.page_content for d in docs)
