"""Fake Provider 复现截断，验证响应元数据、生产子预算和请求级 Trace。"""
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

QUESTION = "采购新品上架需要哪些部门协作？"
VARIANTS = [QUESTION, "参与部门的职责分工与审批", "新品准入所需质检与合规条件"]


@pytest.fixture
def rewrite_env(monkeypatch):
    from backend.infra.llm import proxy
    from backend.observability.tracer import trace_collector
    from backend.rag.retrieval import multi_query as mq

    trace_collector.clear_for_test()
    trace = trace_collector.start(QUESTION, session_id="fake-completion")
    monkeypatch.setattr(proxy, "enforce_model_input_price_tier", lambda *a, **k: None)
    monkeypatch.setattr(mq.prompt_service, "render_sync", lambda *a, **k:
                        SimpleNamespace(text="查询模板", key="rag.multi_query",
                                        version=2, source="pinned",
                                        template_hash="fake-template-hash"))
    yield mq, trace
    trace_collector.clear_for_test()


@pytest.mark.parametrize(
    "limit,tokens,reason,content,truncated,count,status",
    [
        (512, 512, "length", QUESTION, True, 1, "degraded"),
        (1024, 700, "stop", "\n".join(VARIANTS), False, 3, "success"),
        (1024, 1024, None, "\n".join(VARIANTS), True, 1, "degraded"),
        (1024, 500, "max_tokens", QUESTION, True, 1, "degraded"),
        (1024, 100, "stop", QUESTION, True, 1, "degraded"),
        (1024, 200, "stop", "\n".join(VARIANTS[:2]), True, 2, "partial"),
    ],
)
def test_completion_metadata_and_safe_fallback(
    rewrite_env, monkeypatch, limit, tokens, reason, content, truncated, count, status,
):
    mq, trace = rewrite_env
    seen = []

    def invoke(messages, **kwargs):
        seen.append(kwargs)
        return SimpleNamespace(
            content=content,
            response_metadata={"finish_reason": reason, "api_key": "secret-marker",
                               "token_usage": {"completion_tokens": tokens}},
        )

    monkeypatch.setattr(mq, "_rewrite_max_tokens", lambda: limit)
    monkeypatch.setattr(mq, "_resolve_rewrite_llm", lambda: SimpleNamespace(invoke=invoke))
    result = mq._rewrite(QUESTION)
    span = trace.spans[-1]
    metrics = span.metrics
    assert seen == [{"max_tokens": limit}]
    assert len(result) == count and result[0] == QUESTION
    assert span.status == status
    assert metrics["rewrite_truncated"] is truncated
    assert metrics["completion_tokens"] == tokens
    assert metrics["max_tokens"] == limit
    assert metrics["finish_reason"] == (reason or "unknown")
    assert metrics["prompt_hash"] == "fake-template-hash"
    assert metrics["prompt_version"] == 2
    assert metrics["rewrite_ms"] >= 0
    if truncated:
        assert metrics["degrade"] == "rewrite_truncated"
    assert "secret-marker" not in str(metrics)


@pytest.mark.parametrize("remaining,expected", [(8000, 6), (2400, 2.4)])
def test_production_timeout_consumes_shared_remaining_budget(monkeypatch, remaining, expected):
    from backend.config import model_roles, rag
    from backend.infra import thread_pools
    from backend.rag import retrieval_budget
    from backend.rag.retrieval import multi_query as mq

    assert rag.RAG_MULTI_QUERY_REWRITE_TIMEOUT_MS == 6000
    # ee8805f：检索时限 8s→20s 对齐 GPU 重排实测（迟到丢弃曾挤掉精确命中行）。
    assert rag.RAG_RETRIEVAL_DEADLINE_MS == 20000
    waits = []

    class RecordingFuture(Future):
        def result(self, timeout=None):
            waits.append(timeout)
            return [QUESTION]

    monkeypatch.setattr(model_roles, "resolve_runtime_policy", lambda role:
                        model_roles.RoleRuntimeDefaults(timeout_seconds=10))
    monkeypatch.setattr(retrieval_budget, "remaining_ms", lambda limit: remaining)
    monkeypatch.setattr(thread_pools, "submit_rag_task", lambda *a: RecordingFuture())
    assert mq._rewrite_with_budget(QUESTION) == [QUESTION]
    assert waits == [expected]


def test_eval_budget_scope_restores_request_and_never_restarts_deadline():
    from backend.rag.retrieval_budget import (
        clear_retrieval_budget,
        current_budget,
        scoped_retrieval_budget,
    )

    clear_retrieval_budget()
    with pytest.raises(RuntimeError):
        with scoped_retrieval_budget(8000) as outer:
            with scoped_retrieval_budget(8000) as inner:
                assert inner is outer and current_budget() is outer
                raise RuntimeError("模拟检索失败")
    assert current_budget() is None


def test_budget_exhausted_leaves_explicit_fallback_trace(rewrite_env, monkeypatch):
    mq, trace = rewrite_env
    monkeypatch.setattr("backend.rag.retrieval_budget.remaining_ms", lambda limit: 100)
    assert mq._rewrite_with_budget(QUESTION) == [QUESTION]
    assert trace.spans[-1].metrics["degrade"] == "budget_insufficient"
    assert trace.spans[-1].metrics["finish_reason"] == "not_called"


def test_provider_stop_reason_is_parsed(rewrite_env, monkeypatch):
    mq, trace = rewrite_env
    response = SimpleNamespace(content=QUESTION, response_metadata={"stop_reason": "max_tokens"})
    monkeypatch.setattr(mq, "_resolve_rewrite_llm", lambda: SimpleNamespace(invoke=lambda *a, **k: response))
    assert mq._rewrite(QUESTION) == [QUESTION]
    assert trace.spans[-1].metrics["finish_reason"] == "max_tokens"
    assert trace.spans[-1].metrics["rewrite_truncated"] is True
