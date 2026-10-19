"""MultiQuery → Rerank 的请求级查询列表交接（PR #52，Phase B）。

契约：
- 只有真正发生改写（>1 条有效查询）时才把列表交给最终 Rerank；
- 列表只挂在本次请求首个候选的私有元数据上，不写持久化元数据、不进引用；
- 并发/多租户请求之间不得串数据；
- 保留既有 source_query / evidence_groups / fan-out Trace 语义；
- 通过真实 LangChain 的 ContextualCompressionRetriever 验证压缩器能拿到列表。
"""
from __future__ import annotations

import json
from concurrent.futures import Future
from types import SimpleNamespace

import pytest
from langchain_core.documents import Document


@pytest.fixture(autouse=True)
def _clean_env():
    from backend.observability.tracer import trace_collector
    from backend.rag.retrieval_budget import clear_retrieval_budget

    trace_collector.clear_for_test()
    clear_retrieval_budget()
    yield
    trace_collector.clear_for_test()
    clear_retrieval_budget()


def _retriever(monkeypatch, *, queries, docs_by_query, reason="test"):
    import backend.rag.retrieval.multi_query as mq

    monkeypatch.setattr(mq, "need_multi_query", lambda _q: (True, reason))
    monkeypatch.setattr(mq, "_rewrite_with_budget", lambda _q: list(queries))
    monkeypatch.setattr(
        "backend.rag.retrieval_budget.remaining_ms", lambda budget_ms: budget_ms,
    )

    def submit(_pool, run_in_context, invoke, query):
        future = Future()
        future.set_result(invoke(query))
        return future

    monkeypatch.setattr("backend.infra.thread_pools.submit_rag_task", submit)

    def invoke(query):
        return [
            Document(page_content=f"{query}-正文", metadata={"chunk_id": f"{query}-{i}"})
            for i in range(docs_by_query)
        ]

    return mq.MultiQueryRetriever(
        base_retriever=SimpleNamespace(invoke=invoke),
    )


def test_runtime_queries_are_attached_with_source_query_and_groups(monkeypatch):
    queries = ["原始问题", "部门职责视角", "资质合规视角"]
    retriever = _retriever(monkeypatch, queries=queries, docs_by_query=2)

    docs = retriever._get_relevant_documents("原始问题")

    assert len(docs) == 6
    assert docs[0].metadata["_runtime_mq_queries"] == queries
    assert all("_runtime_mq_queries" in d.metadata for d in docs[:1])
    # 只有首个候选携带请求级载荷，其余候选保持干净
    assert all("_runtime_mq_queries" not in d.metadata for d in docs[1:])
    assert docs[0].metadata["source_query"] == "原始问题"
    assert set(docs[0].metadata["_evidence_groups"]) == set(queries)


def test_runtime_queries_absent_when_rewrite_degrades(monkeypatch):
    retriever = _retriever(monkeypatch, queries=["原始问题"], docs_by_query=2)

    docs = retriever._get_relevant_documents("原始问题")

    assert docs and all("_runtime_mq_queries" not in d.metadata for d in docs)


def test_runtime_queries_absent_when_no_candidates(monkeypatch):
    retriever = _retriever(
        monkeypatch, queries=["原始问题", "变体"], docs_by_query=0,
    )

    assert retriever._get_relevant_documents("原始问题") == []


def test_two_requests_do_not_share_query_lists(monkeypatch):
    """同一 retriever 实例被并发请求共享时，载荷不得串到另一请求。"""
    import backend.rag.retrieval.multi_query as mq

    calls = {"n": 0}

    def rewrite(question):
        calls["n"] += 1
        if question == "问题A":
            return ["问题A", "A的部门视角"]
        return ["问题B", "B的资质视角"]

    monkeypatch.setattr(mq, "need_multi_query", lambda _q: (True, "test"))
    monkeypatch.setattr(mq, "_rewrite_with_budget", rewrite)
    monkeypatch.setattr(
        "backend.rag.retrieval_budget.remaining_ms", lambda budget_ms: budget_ms,
    )

    def submit(_pool, run_in_context, invoke, query):
        future = Future()
        future.set_result(invoke(query))
        return future

    monkeypatch.setattr("backend.infra.thread_pools.submit_rag_task", submit)
    retriever = mq.MultiQueryRetriever(base_retriever=SimpleNamespace(
        invoke=lambda q: [Document(page_content=q, metadata={"chunk_id": q})],
    ))

    docs_a = retriever._get_relevant_documents("问题A")
    docs_b = retriever._get_relevant_documents("问题B")

    assert docs_a[0].metadata["_runtime_mq_queries"] == ["问题A", "A的部门视角"]
    assert docs_b[0].metadata["_runtime_mq_queries"] == ["问题B", "B的资质视角"]
    assert docs_a[0] is not docs_b[0]


def test_runtime_queries_are_not_exported_to_trace_or_citations(monkeypatch):
    """私有运行时字段不得出现在 Trace 导出的事件数据里。"""
    from backend.observability.tracer import trace_collector
    from backend.rag.chain import RAGChain

    trace_collector.start("导出检查", session_id="mq-export")
    span = trace_collector.start_span("retrieval", name="检索", type="retrieval")
    docs = [
        Document(page_content="采购部负责供应商准入", metadata={
            "chunk_id": "c1",
            "source_file": "purchase.pdf",
            "source_query": "原始问题",
            "rerank_score": 0.7,
            "_runtime_mq_queries": ["原始问题", "部门职责视角"],
            "_evidence_groups": {"原始问题": []},
        }),
    ]

    RAGChain._record_retrieval_events(None, span, docs)

    dumped = json.dumps(
        [{"name": e["name"], "attributes": e["attributes"]} for e in span.events],
        ensure_ascii=False,
    )
    assert "部门职责视角" not in dumped
    assert "_runtime_mq_queries" not in dumped
    assert "_evidence_groups" not in dumped


def test_compression_layer_receives_queries_end_to_end(monkeypatch):
    """真实 LangChain 压缩器（Rerank 在 MultiQuery 外层）能拿到查询列表。"""
    from langchain_classic.retrievers import ContextualCompressionRetriever

    from backend.rag.reranker import RerankCompressor

    queries = ["采购新品上架需要哪些部门协作？", "部门职责与审批环节", "资质与质检标准"]
    retriever = _retriever(monkeypatch, queries=queries, docs_by_query=2)

    class _RecordingBackend:
        def __init__(self):
            self.ranked_queries = []

        def rank_all(self, query, documents, *, deadline_at=None):
            self.ranked_queries.append(query)
            return [(i, 0.2 + 0.1 * i) for i in range(len(documents))]

    backend = _RecordingBackend()
    comp = RerankCompressor()
    comp.threshold = 0.0
    comp.__dict__["backend"] = backend
    comp.__dict__["_backend_type"] = "dashscope"

    wrapper = ContextualCompressionRetriever(
        base_compressor=comp, base_retriever=retriever,
    )
    out = wrapper.invoke(queries[0])

    assert backend.ranked_queries == queries
    assert out and out[0].metadata["rerank_winner_query"] in queries

def test_production_chain_wrapper_degrade_path_with_expired_budget():
    """生产装配（RAGChain._rerank_wrapper）上验证预算不足时的降级形态。"""
    from backend.rag.chain import RAGChain
    from backend.rag.retrieval_budget import start_retrieval_budget

    chain = RAGChain.__new__(RAGChain)
    chain.doc_db = None
    chain.vectordb = None
    chain.chunk_retriever = None
    chain.bm25 = None
    chain.person_index = {}
    chain._build_retrievers()
    chain._build_chains()

    wrapper = chain._rerank_wrapper
    comp = wrapper.base_compressor

    class _Backend:
        def __init__(self):
            self.queries = []

        def rank_all(self, query, documents, *, deadline_at=None):
            self.queries.append(query)
            return [(i, 0.5) for i in range(len(documents))]

    backend = _Backend()
    comp.threshold = 0.0
    comp.__dict__["backend"] = backend
    comp.__dict__["_backend_type"] = "dashscope"

    question = "采购新品上架需要哪些部门协作？"
    docs = [
        Document(page_content=f"证据{i}", metadata={"chunk_id": f"c{i}"})
        for i in range(3)
    ]
    docs[0].metadata["_runtime_mq_queries"] = [question, "部门职责与审批环节", "资质与质检标准"]

    # 生产检索 deadline 已接近耗尽：只允许评分原始 query，不得再发起变体调用
    start_retrieval_budget(50)
    out = wrapper.base_compressor.compress_documents(docs, question)

    assert backend.queries == [question]
    assert out and out[0].metadata["rerank_status"] == "partial"
