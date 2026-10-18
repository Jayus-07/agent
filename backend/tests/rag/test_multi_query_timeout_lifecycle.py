"""MultiQuery / Rerank 超时、迟到 usage 与关联 Trace 的 Fake Provider 回归。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier, Event, Lock, Timer
from types import SimpleNamespace

import pytest


class _FakeQuotaStore:
    """仅在本测试进程内模拟预占和状态条件结算。"""

    def __init__(self, reserve_gate=None, settle_error=False):
        self.state = "empty"
        self.reservations = []
        self.settlements = []
        self.reviews = []
        self.reserve_gate = reserve_gate
        self.settle_error = settle_error
        self._lock = Lock()

    def reserve(self, **kwargs):
        if self.reserve_gate is not None:
            assert self.reserve_gate.wait(timeout=2)
        with self._lock:
            reservation = SimpleNamespace(reservation_id="reservation-late-1")
            self.state = "reserved"
            self.reservations.append((reservation, kwargs))
            return reservation

    def settle(self, reservation, amount):
        with self._lock:
            if self.settle_error:
                raise RuntimeError("synthetic settlement storage failure")
            if self.state != "reserved":
                return
            self.state = "settled"
            self.settlements.append((reservation.reservation_id, amount))

    def release(self, reservation):
        with self._lock:
            if self.state == "reserved":
                self.state = "released"

    def mark_needs_review(self, reservation, reason):
        with self._lock:
            if self.state != "reserved":
                return False
            self.state = "needs_review"
            self.reviews.append((reservation.reservation_id, reason))
            return True


@pytest.fixture(autouse=True)
def _clear_trace():
    from backend.observability.tracer import trace_collector

    trace_collector.clear_for_test()
    yield
    trace_collector.clear_for_test()


@pytest.mark.parametrize(
    ("settle_error", "expected_settlement"),
    [(False, "settled"), (True, "needs_review")],
    ids=["settled", "settle-failure-kept-for-review"],
)
def test_late_multi_query_usage_settles_original_reservation_once(
    monkeypatch, settle_error, expected_settlement,
):
    """调用方超时不取消已发出的同步请求；迟到 usage 关联原单并只结算一次。"""
    import backend.config.model_roles as model_roles
    import backend.infra.llm.budget as budget
    import backend.infra.llm.proxy as proxy
    import backend.rag.retrieval.multi_query as mq
    from backend.config import rag
    from backend.core.request_context import scoped_tool_identity
    from backend.infra.llm import pricing
    from backend.infra.llm.budget import RequestBudgetLimits
    from backend.observability.tracer import trace_collector

    trace = trace_collector.start("仅用于 Fake Provider 生命周期测试")
    provider_started = Event()
    provider_release = Event()
    reserve_gate = Event()
    quota = _FakeQuotaStore(reserve_gate, settle_error=settle_error)
    usage_rows = []
    futures = []
    executor = ThreadPoolExecutor(max_workers=1)

    class _Provider:
        def invoke(self, *_args, **_kwargs):
            provider_started.set()
            assert provider_release.wait(timeout=3)
            return SimpleNamespace(
                content="第一条查询\n第二条查询",
                response_metadata={
                    "token_usage": {
                        "prompt_tokens": 24,
                        "completion_tokens": 12,
                        "total_tokens": 36,
                    },
                    "finish_reason": "stop",
                    "request_id": "provider-req-safe-01",
                },
            )

    class _Breaker:
        def call(self, fn, *args, **kwargs):
            return fn(*args, **kwargs)

    class _PriceTable:
        def require(self, *_args, **_kwargs):
            return {}

    billing = SimpleNamespace(
        billed_cost_cny=Decimal("0.001000"),
        native_cost=Decimal("0.000100"),
        native_currency="USD",
        fx_rate=Decimal("7.0"),
        price_version="fake-price-v1",
        pricing_source="approved_table",
        usage_source="provider",
        cost_status="exact",
        input_cost_cny=Decimal("0.000070"),
        cached_input_cost_cny=Decimal("0"),
        output_cost_cny=Decimal("0.000030"),
        input_unit_price=Decimal("0.1"),
        output_unit_price=Decimal("0.2"),
        cache_input_unit_price=None,
        reasoning_cost_cny=Decimal("0"),
        cache_write_cost_cny=Decimal("0"),
        tool_call_cost_cny=Decimal("0"),
        reasoning_unit_price=None,
        cache_write_unit_price=None,
        tool_call_unit_price=None,
    )

    monkeypatch.setattr(budget, "_config_mode", lambda: "enforce")
    monkeypatch.setattr(
        budget, "_config_limits",
        lambda: RequestBudgetLimits(max_cost=Decimal("1.00")),
    )
    monkeypatch.setattr(
        "backend.infra.llm.quota.PostgresQuotaStore", lambda: quota,
    )
    monkeypatch.setattr(model_roles, "resolve_runtime_policy", lambda _role:
                        model_roles.RoleRuntimeDefaults(timeout_seconds=10))
    monkeypatch.setattr(model_roles, "has_db_policy", lambda _role: False)
    monkeypatch.setattr(rag, "RAG_MULTI_QUERY_REWRITE_TIMEOUT_MS", 350)
    monkeypatch.setattr(rag, "RAG_RETRIEVAL_DEADLINE_MS", 8000)
    monkeypatch.setattr(proxy, "_enforce_rate_limit", lambda *_args: None)
    monkeypatch.setattr(proxy, "_preflight_context", lambda args, *_a, **_k: args)
    monkeypatch.setattr(proxy, "_resilience_call_identity", lambda:
                        ("fake-chat", "multi_query", "fake-provider"))
    monkeypatch.setattr(proxy, "_breaker_for_resolved_model", lambda *_a: _Breaker())
    monkeypatch.setattr(proxy, "_role_max_retries", lambda _role: 0)
    monkeypatch.setattr(proxy, "_get_provider_for", lambda _model: "fake-provider")
    monkeypatch.setattr(proxy, "canonical_model_id", lambda model: model)
    monkeypatch.setattr(proxy, "get_current_resolved_model", lambda: None)
    monkeypatch.setattr(pricing, "get_current_price_table", lambda *_a: _PriceTable())
    monkeypatch.setattr(pricing, "price_usage", lambda *_a, **_k: billing)
    monkeypatch.setattr(
        "backend.observability.llm_usage_store.get_llm_usage_store",
        lambda: SimpleNamespace(record=lambda event: usage_rows.append(event) or True),
    )
    reserve_timer = Timer(0.02, reserve_gate.set)
    reserve_timer.start()

    bound = proxy._BoundLLMProxy(_Provider(), model_name="fake-chat", role="multi_query")
    bound._bind_context = lambda: None

    class _GateFuture:
        def __init__(self, future):
            self.future = future

        def result(self, timeout=None):
            # 确认 Fake Provider 已进入同步 I/O 后，才开始调用方等待窗口。
            assert provider_started.wait(timeout=2)
            return self.future.result(timeout=timeout)

        def cancel(self):
            return self.future.cancel()

    def submit(_pool, run_in_context, work):
        future = executor.submit(run_in_context, work)
        futures.append(future)
        return _GateFuture(future)

    monkeypatch.setattr("backend.infra.thread_pools.submit_rag_task", submit)

    def fake_rewrite(question):
        attempt = mq._current_rewrite_attempt()
        attempt.update(
            prompt_status="rendered",
            prompt_key="rag.multi_query",
            prompt_version=2,
            prompt_source="snapshot",
            prompt_hash="fake-rendered-template-hash",
        )
        with proxy.scoped_call_diagnostics(mq._record_proxy_diagnostic):
            response = bound.invoke(["safe synthetic prompt"])
        attempt.update(
            provider_request_id=mq._safe_provider_request_id(response),
        )
        attempt.update(rewrite_status="success", query_count=3)
        return [question, "synthetic variant one", "synthetic variant two"]

    monkeypatch.setattr(mq, "_rewrite", fake_rewrite)
    try:
        with scoped_tool_identity(user_id="fake-user", tenant_id="fake-tenant"):
            with budget.scoped_request_budget(
                trace.id, user_id="fake-user", tenant_id="fake-tenant",
            ):
                result = mq._rewrite_with_budget("synthetic original query")
                assert result == ["synthetic original query"]
                assert provider_started.is_set()
                provider_release.set()
                assert futures[0].result(timeout=2) == [
                    "synthetic original query",
                    "synthetic variant one",
                    "synthetic variant two",
                ]
    finally:
        reserve_gate.set()
        provider_release.set()
        reserve_timer.join(timeout=1)
        executor.shutdown(wait=True)

    assert quota.state == expected_settlement
    assert len(quota.reservations) == 1
    if settle_error:
        assert quota.settlements == []
        assert len(quota.reviews) == 1
        assert quota.reviews[0][0] == "reservation-late-1"
    else:
        assert len(quota.settlements) == 1
        assert quota.settlements[0][0] == "reservation-late-1"
    assert len(usage_rows) == 1
    assert usage_rows[0]["tenant_id"] == "fake-tenant"
    assert usage_rows[0]["user_id"] == "fake-user"
    assert usage_rows[0]["request_id"] == trace.id

    main = next(span for span in trace.spans if span.span_id == "query_rewrite")
    late = next(span for span in trace.spans if span.span_id == "query_rewrite_late")
    assert main.status == "timeout"
    assert main.metrics["rewrite_status"] == "degraded"
    assert main.metrics["prompt_hash"] == "fake-rendered-template-hash"
    assert late.status == "completed_late"
    assert late.metrics["caller_outcome"] == "timeout"
    assert late.metrics["provider_completed_late"] is True
    assert late.metrics["reservation_id"] == "reservation-late-1"
    assert late.metrics["usage_ledger_status"] == "written"
    assert late.metrics["settlement_status"] == expected_settlement
    assert late.metrics["budget_reserve_ms"] >= 15
    assert late.metrics["provider_call_ms"] >= 300
    assert isinstance(late.metrics["price_preflight_ms"], (int, float))
    assert "synthetic original query" not in str(main.metrics)


def test_rewrite_queue_wait_and_role_resolution_are_separate(monkeypatch):
    """线程池排队与角色策略解析分别计时，未启动 Provider 不填假耗时。"""
    import backend.config.model_roles as model_roles
    import backend.infra.thread_pools as thread_pools
    import backend.rag.retrieval.multi_query as mq
    from backend.config import rag
    from backend.observability.tracer import trace_collector
    from backend.rag import retrieval_budget

    trace = trace_collector.start("Queue wait synthetic")
    blocker_started = Event()
    release_blocker = Event()
    executor = ThreadPoolExecutor(max_workers=1)

    def occupy_worker():
        blocker_started.set()
        return release_blocker.wait(timeout=2)

    blocker = executor.submit(occupy_worker)
    assert blocker_started.wait(timeout=1)
    release_timer = Timer(0.15, release_blocker.set)
    release_timer.start()

    def slow_policy(_role):
        ready = Event()
        timer = Timer(0.02, ready.set)
        timer.start()
        assert ready.wait(timeout=1)
        timer.join(timeout=1)
        return model_roles.RoleRuntimeDefaults(timeout_seconds=5)

    monkeypatch.setattr(model_roles, "resolve_runtime_policy", slow_policy)
    monkeypatch.setattr(model_roles, "has_db_policy_value", lambda *_a: False)
    monkeypatch.setattr(rag, "RAG_MULTI_QUERY_REWRITE_TIMEOUT_MS", 1000)
    monkeypatch.setattr(rag, "RAG_RETRIEVAL_DEADLINE_MS", 8000)
    monkeypatch.setattr(mq, "_rewrite", lambda question: [
        question, "synthetic variant one", "synthetic variant two",
    ])
    monkeypatch.setattr(
        thread_pools, "submit_rag_task",
        lambda _pool, run_in_context, work: executor.submit(
            run_in_context, work,
        ),
    )
    retrieval_budget.clear_retrieval_budget()
    try:
        assert mq._rewrite_with_budget("synthetic question") == [
            "synthetic question", "synthetic variant one", "synthetic variant two",
        ]
    finally:
        release_blocker.set()
        release_timer.join(timeout=1)
        blocker.result(timeout=1)
        executor.shutdown(wait=True)
        retrieval_budget.clear_retrieval_budget()

    span = next(span for span in trace.spans if span.span_id == "query_rewrite")
    assert span.metrics["role_resolve_ms"] >= 15
    assert span.metrics["queue_wait_ms"] >= 80
    assert isinstance(span.metrics["worker_start_ms"], (int, float))
    assert span.metrics["provider_call_ms"] == "unavailable"
    assert span.metrics["provider_ttfb_ms"] == "unavailable"


def test_prompt_render_delay_is_not_counted_as_provider_time(monkeypatch):
    """Prompt 延迟归入 prompt_render_ms；无 Provider Proxy 就标记不可用。"""
    import backend.config.model_roles as model_roles
    import backend.infra.llm.proxy as proxy
    import backend.rag.retrieval.multi_query as mq
    from backend.observability.tracer import trace_collector

    trace = trace_collector.start("Prompt render synthetic")
    release = Event()
    timer = Timer(0.02, release.set)
    timer.start()
    question = "synthetic original query"
    response = SimpleNamespace(
        content=(
            f"{question}\nsynthetic variant one\nsynthetic variant two"
        ),
        response_metadata={
            "finish_reason": "stop",
            "token_usage": {"completion_tokens": 42, "total_tokens": 42},
        },
    )

    def render(*_args, **_kwargs):
        assert release.wait(timeout=1)
        return SimpleNamespace(
            text="synthetic prompt", key="rag.multi_query", version=4,
            source="snapshot", template_hash="rendered-template-hash-4",
        )

    monkeypatch.setattr(mq.prompt_service, "render_sync", render)
    monkeypatch.setattr(model_roles, "resolve_runtime_name", lambda _role: "fake-chat")
    monkeypatch.setattr(model_roles, "resolve_effective", lambda _role: {"source": "test"})
    monkeypatch.setattr(proxy, "_get_provider_for", lambda _model: "fake-provider")
    monkeypatch.setattr(proxy, "enforce_model_input_price_tier", lambda *_a, **_k: None)
    monkeypatch.setattr(
        mq, "_resolve_rewrite_llm", lambda: SimpleNamespace(
            invoke=lambda *_a, **_k: response,
        ),
    )
    try:
        assert len(mq._rewrite(question)) == 3
    finally:
        release.set()
        timer.join(timeout=1)

    span = next(span for span in trace.spans if span.span_id == "query_rewrite")
    assert span.metrics["prompt_render_ms"] >= 15
    assert span.metrics["model_resolve_ms"] >= 0
    assert span.metrics["price_preflight_ms"] >= 0
    assert span.metrics["prompt_version"] == 4
    assert span.metrics["prompt_hash"] == "rendered-template-hash-4"
    assert span.metrics["provider_call_ms"] == "unavailable"


def test_insufficient_shared_deadline_records_unrendered_prompt_and_skips_worker(
    monkeypatch,
):
    """预算不足时明确保留未渲染状态，且不提交过期 Provider 工作。"""
    import backend.config.model_roles as model_roles
    import backend.infra.thread_pools as thread_pools
    import backend.rag.retrieval.multi_query as mq
    from backend.config import rag
    from backend.observability.tracer import trace_collector
    from backend.rag import retrieval_budget

    trace = trace_collector.start("Insufficient deadline synthetic")
    monkeypatch.setattr(
        model_roles, "resolve_runtime_policy",
        lambda _role: model_roles.RoleRuntimeDefaults(timeout_seconds=10),
    )
    monkeypatch.setattr(model_roles, "has_db_policy_value", lambda *_a: False)
    monkeypatch.setattr(rag, "RAG_MULTI_QUERY_REWRITE_TIMEOUT_MS", 1000)
    monkeypatch.setattr(rag, "RAG_RETRIEVAL_DEADLINE_MS", 8000)
    monkeypatch.setattr(retrieval_budget, "remaining_ms", lambda _limit: 120)
    monkeypatch.setattr(
        thread_pools, "submit_rag_task",
        lambda *_a, **_k: pytest.fail("insufficient budget must not submit worker"),
    )
    monkeypatch.setattr(
        mq.prompt_service, "render_sync",
        lambda *_a, **_k: pytest.fail("insufficient budget must not render prompt"),
    )

    assert mq._rewrite_with_budget("synthetic question") == ["synthetic question"]
    span = next(item for item in trace.spans if item.span_id == "query_rewrite")
    assert span.status == "skipped"
    assert span.metrics["prompt_status"] == "not_rendered"
    assert span.metrics["timeout_kind"] == "shared_deadline_insufficient"
    assert span.metrics["provider_call_started"] is False
    assert span.metrics["usage_status"] == "not_requested"
    assert span.metrics["deadline_remaining_before_ms"] == 120


def test_concurrent_rewrites_keep_tenant_request_and_trace_context_isolated(
    monkeypatch,
):
    """并发改写的租户、请求、Trace 归因各自沿线程池上下文传递。"""
    from concurrent.futures import ThreadPoolExecutor
    from decimal import Decimal

    import backend.config.model_roles as model_roles
    import backend.infra.thread_pools as thread_pools
    import backend.infra.llm.budget as budget
    import backend.rag.retrieval.multi_query as mq
    from backend.config import rag
    from backend.core.request_context import scoped_tool_identity
    from backend.infra.llm.budget import RequestBudgetLimits
    from backend.observability.llm_usage_store import current_usage_attribution
    from backend.observability.tracer import current_trace_context, trace_collector
    from backend.rag import retrieval_budget

    inner = ThreadPoolExecutor(max_workers=2)
    rendezvous = Barrier(2)
    observed = []
    lock = Lock()
    monkeypatch.setattr(
        model_roles, "resolve_runtime_policy",
        lambda _role: model_roles.RoleRuntimeDefaults(timeout_seconds=5),
    )
    monkeypatch.setattr(model_roles, "has_db_policy_value", lambda *_a: False)
    monkeypatch.setattr(rag, "RAG_MULTI_QUERY_REWRITE_TIMEOUT_MS", 2000)
    monkeypatch.setattr(rag, "RAG_RETRIEVAL_DEADLINE_MS", 8000)
    monkeypatch.setattr(budget, "_config_mode", lambda: "observe")
    monkeypatch.setattr(
        budget, "_config_limits",
        lambda: RequestBudgetLimits(max_cost=Decimal("1.00")),
    )
    monkeypatch.setattr(
        "backend.infra.llm.quota.PostgresQuotaStore", lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(
        thread_pools, "submit_rag_task",
        lambda _name, run_in_context, work: inner.submit(run_in_context, work),
    )
    retrieval_budget.clear_retrieval_budget()

    def fake_rewrite(question):
        attribution = current_usage_attribution()
        state = budget.current_request_budget()
        trace_id, _session_id = current_trace_context()
        with lock:
            observed.append((
                attribution["tenant_id"], attribution["user_id"],
                attribution["request_id"], trace_id,
                state.tenant_id, state.user_id,
            ))
        rendezvous.wait(timeout=2)
        return [question, f"{question} variant one", f"{question} variant two"]

    monkeypatch.setattr(mq, "_rewrite", fake_rewrite)

    def run_one(user_id, tenant_id, request_id):
        with scoped_tool_identity(user_id=user_id, tenant_id=tenant_id):
            trace = trace_collector.start(f"{tenant_id} synthetic")
            with budget.scoped_request_budget(
                request_id, user_id=user_id, tenant_id=tenant_id,
            ):
                result = mq._rewrite_with_budget(f"question for {tenant_id}")
            return trace.id, result

    try:
        with ThreadPoolExecutor(max_workers=2) as outer:
            first = outer.submit(run_one, "user-a", "tenant-a", "request-a")
            second = outer.submit(run_one, "user-b", "tenant-b", "request-b")
            results = [first.result(timeout=4), second.result(timeout=4)]
    finally:
        inner.shutdown(wait=True)
        retrieval_budget.clear_retrieval_budget()

    assert len(observed) == 2
    expected = {
        ("tenant-a", "user-a", "request-a", "tenant-a", "user-a"),
        ("tenant-b", "user-b", "request-b", "tenant-b", "user-b"),
    }
    assert {
        (tenant, user, request, tenant_state, user_state)
        for tenant, user, request, _trace, tenant_state, user_state in observed
    } == expected
    trace_ids = {row[3] for row in observed}
    assert len(trace_ids) == 2
    assert trace_ids == {result[0] for result in results}


def test_rerank_response_after_shared_deadline_is_settled_then_discarded(
    monkeypatch,
):
    """共享 deadline 后收到的 rerank 分数不能返回给业务，但 usage 要记账。"""
    import backend.rag.reranker as reranker_module
    from backend.observability.tracer import trace_collector

    trace = trace_collector.start("Rerank late response synthetic")
    entered = Event()
    release = Event()
    deadline_at = __import__("time").monotonic() + 0.30
    accounting = []
    response = SimpleNamespace(
        status_code=200,
        headers={"x-request-id": "safe-rerank-request-02"},
        elapsed=timedelta(milliseconds=12),
        json=lambda: {
            "output": {"results": [{"index": 0, "relevance_score": 0.9}]},
            "usage": {"total_tokens": 48},
        },
    )

    def fake_post(*_args, **_kwargs):
        entered.set()
        assert release.wait(timeout=2)
        return response

    monkeypatch.setattr(reranker_module.requests, "post", fake_post)
    backend = reranker_module.DashScopeReranker(
        "fake-key-never-sent", timeout=1, model="fake-rerank",
        api_format="dashscope", base_url="https://fake.invalid",
        provider_id="fake-provider", billing_model="fake-rerank",
    )
    monkeypatch.setattr(
        backend, "_record_tokens",
        lambda *args, **kwargs: accounting.append((args, kwargs)) or {
            "usage_status": "received",
            "settlement_status": "settlement_attempted",
            "usage_ledger_status": "written",
        },
    )
    timer = Timer(0.32, release.set)
    timer.start()
    try:
        with pytest.raises(reranker_module.RerankLateResultDiscarded):
            backend.rank(
                "synthetic query", ["synthetic evidence"], top_k=1,
                deadline_at=deadline_at,
            )
    finally:
        release.set()
        timer.join(timeout=1)

    assert entered.is_set()
    assert len(accounting) == 1
    span = next(
        span for span in trace.spans
        if span.span_id == "rerank_provider_attempt"
    )
    assert span.status == "completed_late"
    assert span.metrics["late_result_discarded"] is True
    assert span.metrics["timeout_kind"] == "total_deadline_expired"
    assert span.metrics["usage_status"] == "received"
    assert span.metrics["provider_request_id"] == "safe-rerank-request-02"
    assert "synthetic evidence" not in str(span.metrics)


def test_rerank_read_timeout_keeps_usage_unknown_and_marks_review(monkeypatch):
    """ReadTimeout 没有 Provider usage 時保留 unknown/needs_review，不记零成功。"""
    import backend.rag.reranker as reranker_module
    from backend.observability.tracer import trace_collector

    trace = trace_collector.start("Rerank timeout synthetic")
    accounting = []

    def raise_read_timeout(*_args, **_kwargs):
        raise reranker_module.requests.exceptions.ReadTimeout("synthetic timeout")

    backend = reranker_module.DashScopeReranker(
        "fake-key-never-sent", timeout=1, model="fake-rerank",
        api_format="dashscope", base_url="https://fake.invalid",
        provider_id="fake-provider", billing_model="fake-rerank",
    )
    monkeypatch.setattr(reranker_module.requests, "post", raise_read_timeout)
    monkeypatch.setattr(reranker_module, "_budget_allows_extra_query", lambda *_a: False)
    monkeypatch.setattr(
        backend, "_record_tokens",
        lambda *args, **kwargs: accounting.append(kwargs) or {
            "usage_status": "unavailable",
            "settlement_status": "needs_review",
            "usage_ledger_status": "written",
        },
    )
    with pytest.raises(reranker_module.requests.exceptions.ReadTimeout):
        backend.rank(
            "synthetic query", ["synthetic evidence"], top_k=1,
            deadline_at=__import__("time").monotonic() + 2,
        )

    assert accounting == [{"status": "error"}]
    span = next(
        span for span in trace.spans
        if span.span_id == "rerank_provider_attempt"
    )
    assert span.status == "error"
    assert span.metrics["timeout_kind"] == "provider_io_timeout"
    assert span.metrics["provider_completion"] == "unknown"
    assert span.metrics["provider_completed"] is False
    assert span.metrics["provider_cancel_acknowledged"] is False
    assert span.metrics["usage_status"] == "unavailable"
    assert span.metrics["settlement_status"] == "needs_review"
