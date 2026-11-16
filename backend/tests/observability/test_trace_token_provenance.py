# -*- coding: utf-8 -*-
"""Trace 与 llm_usage 明细的 Token/成本追溯测试。"""

import pytest

from backend.observability import llm_usage_store
from backend.observability.tracer import SpanKind, trace_collector


@pytest.fixture(autouse=True)
def _trace_isolation():
    trace_collector.clear_for_test()
    yield
    trace_collector.clear_for_test()


def test_trace_backfills_tokens_cost_and_llm_span_from_usage_store(monkeypatch):
    trace = trace_collector.start("Token provenance", session_id="token-1", workflow_name="agent")
    span = trace_collector.start_span(
        "llm_generate", kind=SpanKind.LLM.value, type="llm_call",
        input={"question": "测试"},
    )
    trace_collector.end_span(span)

    class UsageStore:
        def by_trace(self, trace_id):
            assert trace_id == trace.id
            return [{
                "component": "llm",
                "model": "deepseek-test",
                "provider": "deepseek",
                "prompt_tokens": 120,
                "completion_tokens": 30,
                "total_tokens": 150,
                "cached_tokens": 10,
                "reasoning_tokens": 5,
                "cost_usd": 0.0123,
                "currency": "USD",
                "ts": span.end_time,
                "duration_ms": 24,
            }]

    monkeypatch.setattr(llm_usage_store, "get_llm_usage_store", lambda: UsageStore())

    trace_collector._backfill_usage_from_store(trace)

    assert trace.usage["prompt_tokens"] == 120
    assert trace.usage["completion_tokens"] == 30
    assert trace.usage["total_tokens"] == 150
    assert trace.usage["cost_usd"] == pytest.approx(0.0123)
    assert trace.usage["by_component"]["llm"]["calls"] == 1
    assert trace.cost_usd == pytest.approx(0.0123)
    assert span.metrics["total_tokens"] == 150
    assert span.metrics["token_source"] == "llm_usage_backfill"
    assert span.metrics["model_name"] == "deepseek-test"


def test_trace_backfill_does_not_attach_chat_usage_to_rerank_span(monkeypatch):
    trace = trace_collector.start("Rerank accounting", session_id="token-rerank", workflow_name="rag_eval")
    rerank_span = trace_collector.start_span(
        "rerank_provider_attempt", kind=SpanKind.LLM.value, type="llm_call",
    )
    rerank_span.metrics.update({
        "role": "rerank", "model": "bge-reranker-v2-m3", "provider": "custom-gpu",
    })
    trace_collector.end_span(rerank_span)
    llm_span = trace_collector.start_span(
        "llm_generate", kind=SpanKind.LLM.value, type="llm_call",
    )
    llm_span.metrics["role"] = "llm"
    trace_collector.end_span(llm_span)

    class UsageStore:
        def by_trace(self, trace_id):
            assert trace_id == trace.id
            return [{
                "component": "llm",
                "model": "doubao-test",
                "provider": "test-provider",
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "cost_usd": 0.001,
                "currency": "USD",
                "ts": llm_span.end_time,
                "duration_ms": 25,
            }]

    monkeypatch.setattr(llm_usage_store, "get_llm_usage_store", lambda: UsageStore())
    trace_collector._backfill_usage_from_store(trace)

    assert rerank_span.metrics["role"] == "rerank"
    assert "token_source" not in rerank_span.metrics
    assert "total_tokens" not in rerank_span.metrics
    assert llm_span.metrics["total_tokens"] == 120
    assert llm_span.metrics["model_name"] == "doubao-test"


def test_trace_dto_backfill_keeps_rerank_role_and_model(monkeypatch):
    from backend.app.api.routes._trace_dto import backfill_usage_from_llm_store, to_span_dto, to_trace_dto

    trace = {
        "id": "trace-rerank-dto",
        "duration_ms": 7000,
        "usage": {},
        "cost_usd": None,
        "cost_cny": None,
        "model": "doubao-test",
        "provider": "test-provider",
        "spans": [
            {
                "span_id": "rerank-operation",
                "type": "rerank",
                "name": "重排阶段",
                "status": "error",
                "start_time": "2026-11-12T10:00:00Z",
                "end_time": "2026-11-12T10:00:04Z",
                "duration_ms": 4000,
                "metrics": {},
                "input": {},
                "output": {},
            },
            {
                "span_id": "rerank",
                "type": "llm_call",
                "name": "Rerank Provider 尝试",
                "parent_id": "rerank-operation",
                "status": "error",
                "start_time": "2026-11-12T10:00:00Z",
                "end_time": "2026-11-12T10:00:04Z",
                "duration_ms": 4000,
                "metrics": {
                    "role": "rerank",
                    "model": "bge-reranker-v2-m3",
                    "model_name": "doubao-test",
                    "provider": "custom-gpu",
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                    "cost_usd": 0.001,
                    "cost_cny": 0.0049,
                    "token_source": "llm_usage_backfill",
                },
                "input": {},
                "output": {},
            },
            {
                "span_id": "answer",
                "type": "llm_call",
                "name": "LLM 调用",
                "status": "success",
                "start_time": "2026-11-12T10:00:05Z",
                "end_time": "2026-11-12T10:00:07Z",
                "duration_ms": 2000,
                "metrics": {},
                "input": {},
                "output": {},
            },
        ],
    }

    class UsageStore:
        def by_trace(self, trace_id):
            assert trace_id == trace["id"]
            return [{
                "component": "llm",
                "model": "doubao-test",
                "provider": "test-provider",
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "cost_usd": 0.001,
                "currency": "USD",
                "ts": "2026-11-12T10:00:07Z",
                "duration_ms": 2000,
            }]

    monkeypatch.setattr(llm_usage_store, "get_llm_usage_store", lambda: UsageStore())
    backfill_usage_from_llm_store(trace)

    rerank = to_span_dto(trace["spans"][1], trace["spans"], 7000)
    answer = to_span_dto(trace["spans"][2], trace["spans"], 7000)
    dto = to_trace_dto(trace)

    assert rerank["llm_call"]["role"] == "rerank"
    assert rerank["llm_call"]["model"] == "bge-reranker-v2-m3"
    assert rerank["llm_call"]["provider"] == "custom-gpu"
    assert rerank["llm_call"]["role"] == "rerank"
    assert not ({"prompt_tokens", "completion_tokens", "cost_cny", "prompt_text"} & rerank["llm_call"].keys())
    assert not ({"prompt_tokens", "total_tokens", "cost_cny", "token_source"} & rerank["metrics"].keys())
    assert trace["spans"][1]["metrics"]["token_source"] == "llm_usage_backfill"
    assert answer["llm_call"]["prompt_tokens"] == 100
    assert answer["llm_call"]["model"] == "doubao-test"
    assert dto["summary"]["llm_calls"] == 1
    assert dto["summary"]["retrieval_calls"] == 1
