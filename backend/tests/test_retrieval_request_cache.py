"""请求内重复检索调用缓存测试（P1-5）。

背景（2026-09-03 事故）：MultiQuery 变体 × Stage2 同义词扩展组合出
13 次近似检索。除触发侧治理（P1-6/P1-7）外，同一请求内对相同
(query, metadata_filter, k) 的 ChunkLevelRetriever 调用应命中缓存，
底层向量库/Stage1 检索只执行一次。

约束：
- 缓存随 RequestContext 隔离（contextvars），跨请求不共享；
- 返回的 Document 必须是副本，下游对 metadata 的改写（如 source_query）
  不得污染缓存。
"""
import pytest
from langchain_core.documents import Document
from types import SimpleNamespace


@pytest.fixture(autouse=True)
def _isolate_context(monkeypatch):
    """每个用例独立的请求上下文，且关闭 Adaptive 扩展避免干扰计数。"""
    from backend.rag.context import clear_context
    import backend.config as cfg

    clear_context()
    monkeypatch.setattr(cfg, "ADAPTIVE_RETRIEVAL_ENABLED", False, raising=False)
    yield
    clear_context()


def _make_retriever(hybrid_calls: list):
    """构造最小 ChunkLevelRetriever；hybrid_retrieve 打桩计数。"""
    from backend.rag.retrieval import retrievers as R

    doc_db = SimpleNamespace(
        similarity_search=lambda q, k=5, filter=None: [
            Document(page_content="doc", metadata={"doc_id": "d1"}),
        ],
    )

    def fake_hybrid(query, vector_retriever, bm25_retriever, k=5, doc_ids=None,
                    rrf_k=60, metadata_filter=None, expanded_queries=None):
        hybrid_calls.append(query)
        return [
            Document(
                page_content="退款审核时间为三个工作日，超时请联系客服处理。",
                metadata={"chunk_id": "c1", "doc_id": "d1", "rrf_score": 0.5},
            ),
        ]

    chunk_retriever = SimpleNamespace(
        retrieve=lambda q, k=5, doc_ids=None, metadata_filter=None, expanded_queries=None: [],
    )
    bm25 = SimpleNamespace(invoke=lambda q: [])

    r = R.ChunkLevelRetriever(
        doc_db=doc_db, vectordb=None,
        chunk_retriever=chunk_retriever, bm25=bm25, person_index={},
    )
    return r, fake_hybrid


class TestRequestScopedRetrievalCache:
    def test_identical_query_within_request_retrieves_once(self, monkeypatch):
        """同一请求内相同查询的第二次调用必须命中缓存（底层检索仅一次）。"""
        from backend.rag.retrieval import retrievers as R

        calls = []
        r, fake_hybrid = _make_retriever(calls)
        monkeypatch.setattr(R, "hybrid_retrieve", fake_hybrid)

        docs1 = r._get_relevant_documents("退款审核时间是多少？")
        docs2 = r._get_relevant_documents("退款审核时间是多少？")

        # 缓存命中：第二次调用不得再触发底层检索，且命中计数可观测
        assert R._retrieval_cache_hits() >= 1
        assert docs1 and docs2
        assert [d.metadata.get("chunk_id") for d in docs1] == \
               [d.metadata.get("chunk_id") for d in docs2]

    def test_cached_docs_are_copies_no_metadata_pollution(self, monkeypatch):
        """缓存返回副本：调用方改写 metadata 不得影响后续调用结果。"""
        from backend.rag.retrieval import retrievers as R

        calls = []
        r, fake_hybrid = _make_retriever(calls)
        monkeypatch.setattr(R, "hybrid_retrieve", fake_hybrid)

        docs1 = r._get_relevant_documents("退款审核时间是多少？")
        docs1[0].metadata["source_query"] = "变体A"

        docs2 = r._get_relevant_documents("退款审核时间是多少？")
        assert docs2[0].metadata.get("source_query") is None

    def test_different_query_not_cached(self, monkeypatch):
        """不同查询互不命中缓存。"""
        from backend.rag.retrieval import retrievers as R

        calls = []
        r, fake_hybrid = _make_retriever(calls)
        monkeypatch.setattr(R, "hybrid_retrieve", fake_hybrid)

        r._get_relevant_documents("退款审核时间是多少？")
        n1 = len(calls)
        r._get_relevant_documents("差评怎么处理比较好呢")
        assert len(calls) > n1

    def test_new_request_context_does_not_share_cache(self, monkeypatch):
        """clear_context 后为新请求，缓存不得跨请求命中。"""
        from backend.rag.context import clear_context
        from backend.rag.retrieval import retrievers as R

        calls = []
        r, fake_hybrid = _make_retriever(calls)
        monkeypatch.setattr(R, "hybrid_retrieve", fake_hybrid)

        r._get_relevant_documents("退款审核时间是多少？")
        clear_context()
        r._get_relevant_documents("退款审核时间是多少？")
        # 新请求上下文 → 底层检索必须再次执行
        assert len(calls) > 1
