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
    def test_factory_selects_by_env_mode(self, monkeypatch):
        """工厂按 RERANK_PROVIDER 维度选择后端（P0 契约：不按 SDK 可用性降级）。

        旧测试断言"SDK 缺失自动降级 Local"，与 P0 重构后的设计契约相悖
        （见 get_reranker_backend docstring："不根据 API key 存在与否自动降级"），
        已按现行契约重写。

        2026-09-16：RERANK_PROVIDER 从 ENV_MODE 解耦（留空跟随 ENV_MODE），
        工厂改按 RERANK_PROVIDER 选择。
        """
        from backend.rag import reranker as mod

        # cloud → 云端 Reranker（都用 DB：Key 由数据库专项绑定 + 供应商凭据
        # 解析，即使 SDK 标记缺失也不静默降级）
        from types import SimpleNamespace

        fake_binding = SimpleNamespace(
            provider_id="dashscope-rag",
            adapter="dashscope_rerank",
            model_name="qwen3.7-text-rerank",
            base_url="https://dashscope.aliyuncs.com/api/v1",
        )
        monkeypatch.setattr(mod, "RERANK_PROVIDER", "cloud")
        monkeypatch.setattr(mod, "DASHSCOPE_AVAILABLE", False)
        monkeypatch.setattr(
            mod.specialized_mod,
            "resolve_binding",
            lambda _role: fake_binding if mod.RERANK_PROVIDER == "cloud" else None,
        )
        monkeypatch.setattr(
            mod.credentials_mod,
            "resolve_credentials",
            lambda provider_id, model_name=None: SimpleNamespace(api_key="sk-test"),
        )
        backend = mod.get_reranker_backend()
        assert isinstance(backend, mod.DashScopeReranker)

        # local → Local CrossEncoder（mock 权重加载避免真实加载）
        monkeypatch.setattr(mod, "RERANK_PROVIDER", "local")
        monkeypatch.setattr(
            mod.LocalModelLoader, "get_instance", staticmethod(lambda: object())
        )
        backend = mod.get_reranker_backend()
        assert isinstance(backend, mod.LocalCrossEncoderBackend)
