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
