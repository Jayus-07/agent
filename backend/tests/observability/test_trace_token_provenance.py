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
