"""WP4：请求级调用预算的计数、阻断和统一错误契约。"""

import pytest

from backend.shared.error_protocol import ErrorCode, error_envelope_from_exception


def _limits(**overrides):
    from backend.infra.llm.budget import RequestBudgetLimits

    values = {
        "max_calls": 8,
        "max_total_tokens": 32000,
        "max_retries": 2,
        "max_fallbacks": 1,
    }
    values.update(overrides)
    return RequestBudgetLimits(**values)


def test_budget_blocks_retry_before_model_call():
    from backend.infra.llm.budget import RequestBudget, RequestBudgetExceeded

    budget = RequestBudget(_limits(max_calls=3, max_retries=1), mode="enforce")
    budget.reserve("primary")
    budget.reserve("retry")

    with pytest.raises(RequestBudgetExceeded) as exc_info:
        budget.reserve("retry")

    assert exc_info.value.reason == "request_retries"
    assert budget.snapshot().calls == 2


def test_budget_blocks_fallback_and_token_overrun():
    from backend.infra.llm.budget import RequestBudget, RequestBudgetExceeded

    budget = RequestBudget(
        _limits(max_calls=3, max_total_tokens=10, max_fallbacks=0),
        mode="enforce",
    )
    budget.reserve("primary")
    budget.record_usage(prompt_tokens=6, completion_tokens=4, total_tokens=10)

    with pytest.raises(RequestBudgetExceeded) as token_exc:
        budget.reserve("primary")
    assert token_exc.value.reason == "request_tokens"

    fallback_budget = RequestBudget(_limits(max_fallbacks=1), mode="enforce")
    fallback_budget.reserve("fallback")
    with pytest.raises(RequestBudgetExceeded) as fallback_exc:
        fallback_budget.reserve("fallback")
    assert fallback_exc.value.reason == "request_fallbacks"


def test_budget_observe_mode_records_without_blocking():
    from backend.infra.llm.budget import RequestBudget

    budget = RequestBudget(_limits(max_calls=1), mode="observe")
    budget.reserve("primary")
    budget.reserve("retry")

    snapshot = budget.snapshot()
    assert snapshot.calls == 2
    assert snapshot.retries == 1
    assert snapshot.exceeded == ("request_calls",)


def test_budget_error_maps_to_protocol_budget_exceeded():
    from backend.infra.llm.budget import RequestBudget, RequestBudgetExceeded

    budget = RequestBudget(_limits(max_calls=1), mode="enforce")
    budget.reserve("primary")
    with pytest.raises(RequestBudgetExceeded) as exc_info:
        budget.reserve("primary")

    envelope = error_envelope_from_exception(exc_info.value)
    assert envelope.code is ErrorCode.BUDGET_EXCEEDED
    assert envelope.retryable is False


def test_request_context_binds_shared_budget_by_trace(monkeypatch):
    from backend.core.request_context import RequestContext
    from backend.infra.llm import budget as budget_module
    from backend.observability.tracer import trace_collector

    budget_module.clear_request_budget()
    monkeypatch.setattr(
        budget_module,
        "_config_limits",
        lambda: _limits(max_calls=1),
    )
    monkeypatch.setattr(budget_module, "_config_mode", lambda: "enforce")

    class _Trace:
        id = "trace-budget-1"

    try:
        RequestContext(trace=_Trace()).bind()
        state = budget_module.current_request_budget()
        assert state is not None
        assert state.limits.max_calls == 1
    finally:
        trace_collector.bind(None)
        budget_module.clear_request_budget()


def test_request_budget_off_does_not_create_request_state(monkeypatch):
    from backend.infra.llm import budget as budget_module

    monkeypatch.setattr(budget_module, "_config_mode", lambda: "off")
    budget_module.bind_request_budget("trace-budget-off-1")
    assert budget_module.current_request_budget() is None


def test_token_tracker_shares_request_budget(monkeypatch, tmp_path):
    from backend.infra.llm import budget as budget_module
    from backend.infra.token_tracker import TokenTracker
    from backend.infra.llm.budget import RequestBudgetExceeded

    budget_module.clear_request_budget()
    monkeypatch.setattr(budget_module, "_config_limits", lambda: _limits(max_calls=1))
    monkeypatch.setattr(budget_module, "_config_mode", lambda: "enforce")
    budget_module.bind_request_budget("trace-budget-token-tracker-1")

    tracker = TokenTracker("embedding", str(tmp_path / "tokens.jsonl"))
    monkeypatch.setattr(
        tracker,
        "_extract_usage",
        lambda _result: {
            "prompt_tokens": 3,
            "completion_tokens": 0,
            "total_tokens": 3,
        },
    )
    monkeypatch.setattr(tracker, "_write_jsonl", lambda _event: None)
    monkeypatch.setattr(tracker, "_write_sqlite", lambda _event: None)
    monkeypatch.setattr(tracker, "_record_prometheus", lambda _event: None)
    wrapped = tracker.track(lambda: "ok")

    try:
        assert wrapped() == "ok"
        with pytest.raises(RequestBudgetExceeded):
            wrapped()
        snapshot = budget_module.current_request_budget().snapshot()
        assert snapshot.calls == 1
        assert snapshot.total_tokens == 3
    finally:
        budget_module.clear_request_budget()


def test_proxy_reserves_retry_before_second_model_call(monkeypatch):
    from backend.infra import circuit_breaker
    from backend.infra.llm import budget as budget_module
    from backend.infra.llm import proxy
    from backend.infra.llm.budget import RequestBudgetExceeded

    budget_module.clear_request_budget()
    monkeypatch.setattr(
        budget_module,
        "_config_limits",
        lambda: _limits(max_calls=1, max_retries=2),
    )
    monkeypatch.setattr(budget_module, "_config_mode", lambda: "enforce")
    budget_module.bind_request_budget("trace-budget-proxy-1")
    monkeypatch.setattr(proxy, "LLM_MAX_RETRIES", 1)
    monkeypatch.setattr(proxy, "LLM_RETRY_BACKOFF_BASE", 0)
    monkeypatch.setattr(proxy, "LLM_ALLOW_DEGRADED_ANSWER", False)

    class _Breaker:
        @staticmethod
        def call(attr, *args, **kwargs):
            return attr(*args, **kwargs)

        @staticmethod
        def reset():
            return None

    monkeypatch.setattr(circuit_breaker, "llm_circuit_breaker", _Breaker())
    calls = []

    def always_timeout():
        calls.append("model")
        raise TimeoutError("upstream down")

    try:
        with pytest.raises(RequestBudgetExceeded) as exc_info:
            proxy._call_with_resilience(always_timeout)
        assert exc_info.value.reason == "request_calls"
        assert calls == ["model"]
    finally:
        budget_module.clear_request_budget()
