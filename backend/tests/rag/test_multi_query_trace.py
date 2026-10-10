"""多查询 fan-out 的 Trace 可读性与部分失败留痕。"""
from concurrent.futures import Future
from types import SimpleNamespace

import pytest
from langchain_core.documents import Document


@pytest.fixture(autouse=True)
def _clear_trace_collector():
    from backend.observability.tracer import trace_collector

    trace_collector.clear_for_test()
    yield
    trace_collector.clear_for_test()


def _start_trace():
    from backend.observability.tracer import trace_collector

    trace = trace_collector.start("退款流程", session_id="synthetic-mq-session")
    trace_collector.start_span("root", parent_id=None, name="RAG", type="agent")
    return trace


def _make_retriever(monkeypatch, submit):
    import backend.rag.retrieval.multi_query as multi_query

    monkeypatch.setattr(multi_query, "need_multi_query", lambda _query: (True, "test"))
    monkeypatch.setattr(
        multi_query, "_rewrite_with_budget", lambda _query: ["退款申请流程", "退款多久到账"]
    )
    monkeypatch.setattr("backend.infra.thread_pools.submit_rag_task", submit)
    monkeypatch.setattr(
        "backend.rag.retrieval_budget.remaining_ms", lambda budget_ms: budget_ms
    )
    return multi_query.MultiQueryRetriever(base_retriever=SimpleNamespace(invoke=lambda _q: []))


def _fanout_span(trace):
    return next(span for span in trace.spans if span.span_id == "multi_query_fanout")


def test_fanout_trace_maps_each_rewrite_to_retrieval_counts(monkeypatch):
    """每条改写查询都应留下成功状态、原始命中数和去重保留数。"""
    trace = _start_trace()

    def submit(_pool_name, _run_in_context, _invoke, query):
        future = Future()
        chunk_id = "shared" if query == "退款多久到账" else "apply"
        docs = [Document(page_content="证据", metadata={"chunk_id": chunk_id})]
        if query == "退款多久到账":
            docs.append(Document(page_content="重复证据", metadata={"chunk_id": "apply"}))
        future.set_result(docs)
        return future

    retriever = _make_retriever(monkeypatch, submit)
    retriever._get_relevant_documents("退款怎么处理？")

    span = _fanout_span(trace)
    results = span.output["query_results"]
    assert [item["query"] for item in results] == ["退款申请流程", "退款多久到账"]
    assert [item["status"] for item in results] == ["success", "success"]
    assert [item["retrieved_docs"] for item in results] == [1, 2]
    # 哪个查询先返回，重复 chunk 会归属哪个查询，因此各查询的去重数可变；
    # 总保留数必须与去重后的结果集合一致。
    assert sum(item["retained_docs"] for item in results) == 2
    assert span.metrics["query_count"] == 2
    assert span.metrics["unique_docs"] == 2


def test_fanout_trace_marks_unfinished_variant_as_timeout(monkeypatch):
    """fan-out 截止后，已完成和超时的查询在同一 Trace 中分别可见。"""
    trace = _start_trace()

    def submit(_pool_name, _run_in_context, _invoke, query):
        future = Future()
        if query == "退款申请流程":
            future.set_result([Document(page_content="证据", metadata={"chunk_id": "apply"})])
        return future

    monkeypatch.setattr("backend.config.rag.RAG_MULTI_QUERY_FANOUT_TIMEOUT_MS", 2)
    retriever = _make_retriever(monkeypatch, submit)
    retriever._get_relevant_documents("退款怎么处理？")

    span = _fanout_span(trace)
    results = span.output["query_results"]
    assert [item["status"] for item in results] == ["success", "timeout"]
    assert span.status == "timeout"
    assert span.metrics["timeout_count"] == 1


def test_final_rag_context_trace_keeps_source_query_for_each_chunk():
    """最终进入生成上下文的 chunk 应能追溯到贡献它的改写查询。"""
    from backend.observability.tracer import trace_collector
    from backend.rag.chain import RAGChain

    trace = _start_trace()
    retrieval_span = trace_collector.start_span(
        "retrieval", name="检索", type="retrieval"
    )
    documents = [Document(
        page_content="退款需提交申请",
        metadata={
            "chunk_id": "refund-policy-1",
            "source_file": "退款制度",
            "source_query": "退款申请流程",
        },
    )]

    RAGChain._record_retrieval_events(None, retrieval_span, documents)

    final_context = next(
        event for event in retrieval_span.events if event["name"] == "final_context"
    )
    assert final_context["attributes"]["chunks"][0]["source_query"] == "退款申请流程"
    assert trace.spans[0].span_id == "root"


def test_fanout_submission_failure_closes_trace_span_before_propagating(monkeypatch):
    """检索线程池拒绝任务时，Trace 不能留下 running 状态的假 span。"""
    trace = _start_trace()

    def submit(*_args):
        raise RuntimeError("检索线程池不可用")

    retriever = _make_retriever(monkeypatch, submit)
    with pytest.raises(RuntimeError, match="检索线程池不可用"):
        retriever._get_relevant_documents("退款怎么处理？")

    span = _fanout_span(trace)
    assert span.status == "error"
    assert any("检索线程池不可用" in event["message"] for event in span.events)


def test_partial_query_failure_does_not_mark_successful_fanout_as_total_error(monkeypatch):
    """部分变体失败应标记 partial，避免把仍可回答的整条 Trace 判成失败。"""
    trace = _start_trace()

    def submit(_pool_name, _run_in_context, _invoke, query):
        future = Future()
        if query == "退款申请流程":
            future.set_result([Document(page_content="证据", metadata={"chunk_id": "apply"})])
        else:
            future.set_exception(RuntimeError("搜索源暂不可用"))
        return future

    retriever = _make_retriever(monkeypatch, submit)
    retriever._get_relevant_documents("退款怎么处理？")

    span = _fanout_span(trace)
    assert span.status == "partial"
    assert [item["status"] for item in span.output["query_results"]] == [
        "success", "error"
    ]
