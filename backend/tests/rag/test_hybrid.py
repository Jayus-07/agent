"""test_hybrid.py — 混合检索的 metadata_filter 过滤。"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from langchain_core.documents import Document

from backend.rag.retrieval.hybrid import _filter_by_metadata, _hybrid_retrieve_impl


def _doc(meta):
    return Document(page_content="x", metadata=meta)


def test_filter_by_metadata_kb_id():
    """BM25 结果必须按 kb_id 过滤，隔离不同知识库的文档。"""
    docs = [
        _doc({"kb_id": "rag_test_kb", "doc_id": "a"}),
        _doc({"kb_id": "policy_general", "doc_id": "b"}),
        _doc({"kb_id": "rag_test_kb", "doc_id": "c"}),
    ]
    filtered = _filter_by_metadata(docs, {"kb_id": "rag_test_kb"})
    assert [d.metadata["doc_id"] for d in filtered] == ["a", "c"]


def test_filter_by_metadata_no_filter_keeps_all():
    """无 filter 时原样返回。"""
    docs = [_doc({"kb_id": "rag_test_kb"}), _doc({"kb_id": "policy_general"})]
    assert _filter_by_metadata(docs, None) == docs
    assert _filter_by_metadata(docs, {}) == docs


def test_filter_by_metadata_multi_kv():
    """多条件过滤：所有 kv 都需匹配。"""
    docs = [
        _doc({"kb_id": "rag_test_kb", "doc_type": "policy"}),
        _doc({"kb_id": "rag_test_kb", "doc_type": "faq"}),
    ]
    filtered = _filter_by_metadata(docs, {"kb_id": "rag_test_kb", "doc_type": "faq"})
    assert len(filtered) == 1
    assert filtered[0].metadata["doc_type"] == "faq"


def test_hybrid_span_exposes_canonical_retrieval_counts(monkeypatch):
    """检索 span 必须提供四路召回计数，供质量验收定位 BM25 贡献。"""
    import backend.config.rag as rag_config
    import backend.observability.tracer as tracer_module
    import backend.rag.retrieval.hybrid as hybrid_module

    monkeypatch.setattr(rag_config, "ADAPTIVE_THRESHOLD_ENABLED", False)
    monkeypatch.setattr(rag_config, "CONFIDENCE_AGGREGATOR_ENABLED", False)
    monkeypatch.setattr(hybrid_module, "_classify_query_tier", lambda _query: "hybrid")
    monkeypatch.setattr(
        "backend.rag.retrieval.query_router.route",
        lambda _query: {"query_type": "identifier", "vector_weight": 1.0,
                        "bm25_weight": 1.0, "signals": [], "enabled": True},
    )
    monkeypatch.setattr(
        hybrid_module,
        "_evaluate_retrieval_gate",
        lambda _docs, _query: SimpleNamespace(to_metrics=lambda: {"gate_passed": True}),
    )

    span = SimpleNamespace(metrics={})
    collector = MagicMock()
    collector.start_span.return_value = span
    monkeypatch.setattr(tracer_module, "trace_collector", collector)

    vector_docs = [
        _doc({"chunk_id": "v1", "doc_id": "d1"}),
        _doc({"chunk_id": "v2", "doc_id": "d2"}),
    ]
    bm25_docs = [
        _doc({"chunk_id": "b1", "doc_id": "d3"}),
        _doc({"chunk_id": "b2", "doc_id": "d4"}),
        _doc({"chunk_id": "b3", "doc_id": "d5"}),
    ]
    vector = MagicMock()
    vector.retrieve.return_value = vector_docs
    bm25 = MagicMock()
    bm25.invoke.return_value = bm25_docs

    result = _hybrid_retrieve_impl("SKU ABC-123", vector, bm25, k=4)

    assert len(result) == 4
    metrics = collector.end_span.call_args.kwargs["metrics"]
    assert metrics["vector_count"] == 2
    assert metrics["bm25_count"] == 3
    assert metrics["fused_count"] == 4
    assert metrics["rerank_count"] == 4
