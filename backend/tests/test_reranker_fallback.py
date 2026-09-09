"""RerankCompressor 降级策略测试（P0 修复：异常降级必须透传原文档）。

背景（2026-09-03 生产事故）：dashscope SDK 缺失时 RerankCompressor 捕获
异常后静默返回空列表，把全部召回结果清空，导致 Evidence Gate 误判
no_evidence 拒答。降级契约：重排是增强组件，失败不得减少召回数量。
"""
import pytest
from langchain_core.documents import Document


def _docs(n=4):
    return [
        Document(page_content=f"内容{i}", metadata={"chunk_id": f"c{i}"})
        for i in range(n)
    ]


class _FailingBackend:
    """模拟重排后端调用失败。"""

    def compress_documents(self, documents, query, **kwargs):
        raise RuntimeError("模拟后端故障")


class TestRerankCompressorFallback:
    def test_backend_failure_returns_original_docs(self):
        """后端异常 → 透传原文档（而非空列表）。"""
        from backend.rag.reranker import RerankCompressor

        c = RerankCompressor()
        c.__dict__["backend"] = _FailingBackend()
        c.__dict__["_backend_type"] = "dashscope"

        docs = _docs(4)
        result = c.compress_documents(docs, "退款审核时间是多少？")

        assert len(result) == 4
        assert [d.metadata["chunk_id"] for d in result] == ["c0", "c1", "c2", "c3"]

    def test_backend_failure_respects_top_k(self):
        """透传时同样受 top_k 限制，避免上下文膨胀。"""
        from backend.rag.reranker import RerankCompressor

        c = RerankCompressor()
        c.top_k = 2
        c.__dict__["backend"] = _FailingBackend()
        c.__dict__["_backend_type"] = "dashscope"

        result = c.compress_documents(_docs(6), "查询")
        assert len(result) == 2

    def test_backend_init_failure_also_returns_original_docs(self):
        """后端构造失败（如 SDK 缺失）→ 同样透传原文档。"""
        from backend.rag import reranker as rk

        c = rk.RerankCompressor()

        def _broken_factory():
            raise RuntimeError("DashScope API requires the 'dashscope' package.")

        import backend.rag.reranker as mod
        orig = mod.get_reranker_backend
        mod.get_reranker_backend = _broken_factory
        try:
            result = c.compress_documents(_docs(3), "查询")
        finally:
            mod.get_reranker_backend = orig

        assert len(result) == 3


class TestBackendFactoryFallback:
    def test_missing_sdk_falls_back_to_local(self, monkeypatch):
        """配置了 dashscope 但 SDK 缺失 → 工厂降级本地模型而非抛异常。"""
        from backend.rag import reranker as mod

        monkeypatch.setattr(mod, "DASHSCOPE_AVAILABLE", False)
        monkeypatch.setenv("RERANKER_BACKEND", "dashscope")
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
        # 避免真实加载 CrossEncoder 权重
        monkeypatch.setattr(
            mod.LocalModelLoader, "get_instance", staticmethod(lambda: object())
        )

        backend = mod.get_reranker_backend()
        assert isinstance(backend, mod.LocalCrossEncoderBackend)
