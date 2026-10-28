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


def test_legacy_chunks_without_tenant_metadata_are_default_only():
    docs = [
        _doc({"doc_id": "legacy"}),
        _doc({"doc_id": "default", "tenant_id": "default"}),
        _doc({"doc_id": "tenant-a", "tenant_id": "tenant-a"}),
    ]
    default_docs = _filter_by_metadata(docs, {"tenant_id": "default"})
    other_tenant_docs = _filter_by_metadata(docs, {"tenant_id": "tenant-b"})

    assert [doc.metadata["doc_id"] for doc in default_docs] == ["legacy", "default"]
    assert other_tenant_docs == []


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


def test_enhanced_branch_injects_gate_decision(monkeypatch):
    """2026-10-25 回归：enhanced 分支此前直接 return，绕过尾部 Gate 注入
    → chain Gate 1 injected=None → 实体覆盖校验退化到 rerank 后子集重算
    （实测「信息安全管理制度」问答误拒）。enhanced 返回的每个候选都必须
    带 __evidence_gate_decision__，且为全量注入（任意候选被 Rerank 淘汰
    后注入仍在幸存者上）。"""

    def fake_enhanced_retrieve(query, vector, bm25, **kwargs):
        docs = [
            _doc({"chunk_id": "e1", "doc_id": "d1"}),
            _doc({"chunk_id": "e2", "doc_id": "d2"}),
        ]
        return docs, {"confidence_score": 0.8}

    import backend.rag.retrieval.hybrid as hybrid_module
    import backend.rag.retrieval.enhanced_hybrid_retrieval as enhanced_module
    from backend.config import rag as rag_config

    monkeypatch.setattr(rag_config, "ADAPTIVE_THRESHOLD_ENABLED", True)
    monkeypatch.setattr(rag_config, "CONFIDENCE_AGGREGATOR_ENABLED", True)
    monkeypatch.setattr(
        enhanced_module, "enhanced_hybrid_retrieve", fake_enhanced_retrieve)
    monkeypatch.setattr(
        hybrid_module,
        "_evaluate_retrieval_gate",
        lambda _docs, _query: SimpleNamespace(
            to_metrics=lambda: {"gate_passed": True, "entity_check": "pass"}),
    )

    result = _hybrid_retrieve_impl("查制度", MagicMock(), MagicMock(), k=2)

    assert [d.metadata["chunk_id"] for d in result] == ["e1", "e2"]
    for d in result:
        injected = d.metadata.get("__evidence_gate_decision__")
        assert injected is not None, "enhanced 分支候选缺 Gate 注入"
        assert injected["entity_check"] == "pass"


def test_identifier_exact_hit_boost_rescues_bm25_unique_row(monkeypatch):
    """2026-10-28 回归：查询含长标识符（SKU/单号）时，仅被 BM25 精确命中
    的候选不得被 RRF 结构性埋没（同模板表格行两路都出现，得分必然更高，
    精确行曾进不了 top5 → AI 答"未提及"）。标识符原文精确命中给等效
    单路第 1 名的加性奖励，进入 rerank 视野由重排终裁。"""
    import backend.config.rag as rag_config
    import backend.observability.tracer as tracer_module
    import backend.rag.retrieval.hybrid as hybrid_module

    monkeypatch.setattr(rag_config, "ADAPTIVE_THRESHOLD_ENABLED", False)
    monkeypatch.setattr(rag_config, "CONFIDENCE_AGGREGATOR_ENABLED", False)
    monkeypatch.setattr(hybrid_module, "_classify_query_tier", lambda _q: "hybrid")
    monkeypatch.setattr(
        "backend.rag.retrieval.query_router.route",
        lambda _q: {"query_type": "identifier", "vector_weight": 1.0,
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

    # 向量路：同模板相似行（无 000001），模板文本几乎相同
    vector_docs = []
    for i in range(1, 6):
        d = _doc({"chunk_id": f"v{i}", "doc_id": f"d{i}"})
        d.page_content = f"[产品] SKU 0000{i:02d} 名称 商品{i} 价格 {i * 10}"
        vector_docs.append(d)
    # BM25 路：精确行排第 1，其后是相似行
    exact = _doc({"chunk_id": "b-exact", "doc_id": "dx"})
    exact.page_content = "[产品] SKU 000001 名称 商品1 价格 311 【原始行】 000001, 商品1, 311"
    bm25_docs = [exact] + vector_docs
    vector = MagicMock()
    vector.retrieve.return_value = vector_docs
    bm25 = MagicMock()
    bm25.invoke.return_value = bm25_docs

    result = _hybrid_retrieve_impl(
        "产品工作表中 SKU 000001 的商品名称和价格是什么？", vector, bm25, k=5)

    ids = [d.metadata["chunk_id"] for d in result]
    assert "b-exact" in ids, f"精确行必须进入 rerank 视野，实际: {ids}"
