"""Rerank 与 MultiQuery 组装顺序测试（P2-11）。

背景（2026-09-03 事故）：RerankCompressor 被包在 MultiQueryRetriever
内层，每个改写变体各自触发一次重排（3 变体 = 3 次 rerank LLM/API 调用），
且变体间的文档在重排前未合并去重，阈值过滤也是按变体局部进行。

修复：Rerank 移到 MultiQuery 外层 —— 变体先各自检索合并去重，
全局重排只在合并结果上执行一次。

装配顺序（外→内）应为：
  HistoryAware(可选) → ContextualCompressionRetriever(RerankCompressor)
  → MultiQueryRetriever → AdaptiveRetriever → ChunkLevelRetriever
"""
import pytest


@pytest.fixture
def built_chain():
    """bypass 重型初始化，仅装配检索链结构。"""
    from backend.rag.chain import RAGChain

    chain = RAGChain.__new__(RAGChain)
    chain.doc_db = None
    chain.vectordb = None
    chain.chunk_retriever = None
    chain.bm25 = None
    chain.person_index = {}
    chain._build_retrievers()
    chain._build_chains()
    return chain


def _find_retriever(root, cls):
    """沿 base_retriever 链查找首个指定类型实例。"""
    node = root
    while node is not None:
        if isinstance(node, cls):
            return node
        node = getattr(node, "base_retriever", None)
    return None


class TestRerankOutsideMultiQuery:
    def test_rerank_wraps_multiquery_not_inside(self, built_chain):
        """Rerank 必须在 MultiQuery 外层：MultiQuery 的下游链路中不得再有压缩器。"""
        from langchain_classic.retrievers import ContextualCompressionRetriever
        from backend.rag.retrieval.multi_query import MultiQueryRetriever

        mq = built_chain._mq_retriever
        assert isinstance(mq, MultiQueryRetriever)

        # MultiQuery 内层（base_retriever 链）不得再包含 ContextualCompressionRetriever
        assert _find_retriever(mq.base_retriever, ContextualCompressionRetriever) is None

    def test_multiquery_inner_chain_is_adaptive(self, built_chain):
        """MultiQuery 内层应为 AdaptiveRetriever（变体只做检索，不做重排）。"""
        from backend.rag.retrieval.retrievers import AdaptiveRetriever

        mq = built_chain._mq_retriever
        assert isinstance(mq.base_retriever, AdaptiveRetriever)

    def test_rerank_present_above_multiquery(self, built_chain):
        """整条链上 Rerank 压缩器必须存在且位于 MultiQuery 上游。"""
        from langchain_classic.retrievers import ContextualCompressionRetriever
        from backend.rag.retrieval.multi_query import MultiQueryRetriever
        from backend.rag.reranker import RerankCompressor

        wrapper = built_chain._rerank_wrapper
        assert isinstance(wrapper, ContextualCompressionRetriever)
        assert isinstance(wrapper.base_compressor, RerankCompressor)
        # Rerank 包装层必须直接包在 MultiQuery 外层（合并后只重排一次）
        assert wrapper.base_retriever is built_chain._mq_retriever
        assert isinstance(wrapper.base_retriever, MultiQueryRetriever)
