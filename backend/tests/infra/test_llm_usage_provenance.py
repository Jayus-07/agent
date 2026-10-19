"""LLM 用量记录的处理运行归属测试。"""

from backend.observability.llm_usage_store import current_usage_attribution
from backend.shared.processing_context import ProcessingBinding, bind_processing


def test_usage_attribution_includes_processing_binding():
    from backend.infra.llm import budget
    from backend.observability.tracer import trace_collector

    binding = ProcessingBinding(
        run_id="run-1", step_id="step-1", role="metadata_extract", stage="metadata_extract"
    )

    trace_collector.clear_for_test()
    request_token = budget._current_request_id.set("")
    try:
        with bind_processing(binding):
            attribution = current_usage_attribution()
    finally:
        budget._current_request_id.reset(request_token)
        trace_collector.clear_for_test()

    assert attribution["run_id"] == "run-1"
    assert attribution["step_id"] == "step-1"
    assert attribution["role"] == "metadata_extract"
    assert attribution["stage"] == "metadata_extract"
    assert attribution["request_id"] == "run-1"


def test_usage_attribution_falls_back_to_bound_background_request_id(monkeypatch):
    from backend.core.request_context import scoped_tool_identity
    from backend.infra.llm import budget
    from backend.observability.tracer import trace_collector

    trace_collector.clear_for_test()
    monkeypatch.setattr("backend.config.llm.LLM_BUDGET_MODE", "observe")
    monkeypatch.setattr(
        "backend.infra.llm.quota.PostgresQuotaStore", lambda: object(),
    )
    try:
        with scoped_tool_identity(
            user_id="worker-user", tenant_id="worker-tenant",
        ):
            with budget.scoped_request_budget(
                "index-run-request-1",
                user_id="worker-user",
                tenant_id="worker-tenant",
            ):
                attribution = current_usage_attribution()

        assert attribution["user_id"] == "worker-user"
        assert attribution["tenant_id"] == "worker-tenant"
        assert attribution["request_id"] == "index-run-request-1"
    finally:
        trace_collector.clear_for_test()


def test_usage_attribution_prefers_budget_id_for_settlement_correlation(monkeypatch):
    from types import SimpleNamespace

    from backend.infra.llm import budget
    from backend.observability.tracer import trace_collector

    trace_collector.clear_for_test()
    trace = SimpleNamespace(
        id="trace-id-1",
        request_id="trace-request-1",
        session_id="trace-session-1",
    )
    monkeypatch.setattr(trace_collector, "current", lambda: trace)
    request_token = budget._current_request_id.set("budget-request-1")
    try:
        attribution = current_usage_attribution()
    finally:
        budget._current_request_id.reset(request_token)
        trace_collector.clear_for_test()

    assert attribution["request_id"] == "budget-request-1"
    assert attribution["trace_id"] == "trace-id-1"
