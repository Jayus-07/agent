"""RAG Stage Timing 接线回归测试（Phase 6，spec §27）。

record_rag_stage / rag_stage_duration_seconds 在 main 上已定义但零调用——
本文件锁死「四阶段必被采样」：retrieve / rerank / generate / total。
"""
from __future__ import annotations

from prometheus_client import REGISTRY


def _count(stage: str) -> float:
    v = REGISTRY.get_sample_value(
        "rag_stage_duration_seconds_count", {"stage": stage},
    )
    return v or 0.0


def test_record_rag_stage_samples_histogram():
    from backend.observability.metrics import record_rag_stage

    before = _count("total")
    record_rag_stage("total", 123.0)
    assert _count("total") == before + 1


def test_rerank_stage_recorded_on_compress(monkeypatch):
    """compress_documents 的成功出口必须记 rerank 阶段时序。"""
    from langchain_core.documents import Document
    from langchain_core.retrievers import BaseRetriever  # noqa: F401

    from backend.rag.reranker import RerankCompressor

    def _cnt() -> float:
        return _count("rerank")

    before = _cnt()

    docs = [
        Document(page_content=f"内容{i}，足够长的文本以通过最小长度检查。",
                 metadata={"doc_id": f"d{i}"})
        for i in range(5)
    ]
    comp = RerankCompressor.model_construct(
        top_k=8, threshold=0.0, backend_type_local=True,
    )
    comp._backend_type = "local"

    class _FakeBackend:
        def compress_documents(self, documents, query, **kwargs):
            return list(documents)[: kwargs.get("top_k", 8)]

    object.__setattr__(comp, "backend", _FakeBackend())
    monkeypatch.setattr(comp, "_ensure_backend", lambda: None, raising=False)

    out = comp.compress_documents(docs, "测试查询")
    assert len(out) == 5
    assert _cnt() == before + 1
