"""B1-B LLM 调用治理收口定向测试（travel llm_intent + eval judge + 评测租户隔离）

对应第四阶段审计 P0-2 的 Travel + Eval 部分（rag multi_query 归 RAG 优化线，
本文件不覆盖）：

- proxy 新出口 ``bind_llm_for_role``：角色解析/参数绑定/无绑定 fail-fast
- 治理链：_BoundLLMProxy.invoke 发起前预算预占、调用后计量（mock 捕获，
  真实链路证据见交付报告验收脚本）
- travel llm_intent：走治理出口（main 角色 + temperature=0/max_tokens=1024），
  角色无绑定回落词表（既有降级语义不变）
- eval judge：走 eval_gen 角色；未配置 → total=0.0 明确失败（契约不变）；
  正常路径 evaluator 统计不退化
- 评测计费主体：tenant="eval" 与生产租户隔离，且与 llm_usage 归因
  （current_usage_attribution）同一 ContextVar 来源——三方可对账的前提
"""
from types import SimpleNamespace

import pytest

import backend.infra.llm.proxy as proxy_mod


# ─────────────────────────────────────────────────────────────
# 1. bind_llm_for_role 契约
# ─────────────────────────────────────────────────────────────


def test_bind_llm_for_role_requires_bound_model(monkeypatch):
    """角色无绑定模型 → RuntimeError（调用方按自身语义降级）。"""
    from backend.config import model_roles

    monkeypatch.setattr(model_roles, "resolve_effective",
                        lambda role: {"role": role, "value": ""})
    with pytest.raises(RuntimeError, match="没有可用模型"):
        proxy_mod.bind_llm_for_role("eval_gen")


def test_bind_llm_for_role_registers_role_and_params(monkeypatch):
    """返回 _BoundLLMProxy：role/模型登记正确，temperature/max_tokens 绑定生效。"""
    from backend.config import model_roles

    monkeypatch.setattr(model_roles, "resolve_effective",
                        lambda role: {"role": role, "value": "test-model"})
    captured: dict = {}

    class _FakeInner:
        def bind(self, **kwargs):
            captured["bind_kwargs"] = kwargs
            return SimpleNamespace(invoke=lambda *a, **k: None,
                                   kwargs=kwargs)

    monkeypatch.setattr(proxy_mod, "_get_override_llm",
                        lambda name: captured.setdefault("inner", _FakeInner()))
    wrapped = proxy_mod.bind_llm_for_role(
        "eval_gen", temperature=0.1, max_tokens=512)

    assert type(wrapped).__name__ == "_BoundLLMProxy"
    assert wrapped._role == "eval_gen"
    assert wrapped._model_name == "test-model"
    assert captured["bind_kwargs"] == {"temperature": 0.1, "max_tokens": 512}
    # 登记调用上下文（record_llm_result / usage 结算按此归属）
    ctx = proxy_mod.get_current_resolved_model()
    assert ctx is not None and ctx.role == "eval_gen"


def test_bound_proxy_invoke_goes_through_governance(monkeypatch):
    """invoke 走治理链：发起前预算预占 → 真实调用 → 用量计量，顺序固定。"""
    from backend.config import model_roles

    monkeypatch.setattr(model_roles, "resolve_effective",
                        lambda role: {"role": role, "value": "test-model"})
    calls: list[str] = []

    class _FakeInner:
        def bind(self, **kwargs):
            return self

        def invoke(self, messages):
            calls.append("invoke")
            return SimpleNamespace(content="ok", usage_metadata={
                "input_tokens": 10, "output_tokens": 5})

    monkeypatch.setattr(proxy_mod, "_get_override_llm", lambda name: _FakeInner())
    monkeypatch.setattr(
        proxy_mod, "_call_with_resilience",
        lambda fn, *a, **k: (calls.append("resilience"), fn(*a, **k))[1])
    monkeypatch.setattr(
        proxy_mod, "_record_tokens",
        lambda result, **kw: calls.append(
            f"record:{kw.get('model_name')}"))

    wrapped = proxy_mod.bind_llm_for_role("main", temperature=0)
    resp = wrapped.invoke(["hi"])

    assert resp.content == "ok"
    # 顺序：预占/韧性链在真实调用前，计量在调用后（模型名随包装登记）
    assert calls == ["resilience", "invoke", "record:test-model"], calls


# ─────────────────────────────────────────────────────────────
# 2. travel llm_intent 治理化
# ─────────────────────────────────────────────────────────────


def _enable_intent(monkeypatch):
    from backend.config import travel as T

    monkeypatch.setattr(T, "TRAVEL_LLM_INTENT_ENABLED", True)


def test_intent_uses_governed_entry_with_main_role(monkeypatch):
    """classify_intent_llm 经 bind_llm_for_role("main") 且带确定性参数。"""
    _enable_intent(monkeypatch)
    from backend.travel.services import llm_intent_service as svc

    captured: dict = {}

    def fake_bind(role, *, temperature=None, max_tokens=None):
        captured["role"] = role
        captured["temperature"] = temperature
        captured["max_tokens"] = max_tokens
        return SimpleNamespace(
            invoke=lambda messages: SimpleNamespace(
                content='{"family": "query"}'))

    monkeypatch.setattr("backend.infra.llm.proxy.bind_llm_for_role", fake_bind)
    got = svc.classify_intent_llm("泉州有什么特产", has_itinerary=True)
    assert got == "query_dynamic"
    assert captured == {"role": "main", "temperature": 0, "max_tokens": 1024}


def test_intent_falls_back_when_role_unbound(monkeypatch):
    """main 角色无模型绑定（RuntimeError）→ 回落词表返回 None（降级语义不变）。"""
    _enable_intent(monkeypatch)
    from backend.travel.services import llm_intent_service as svc

    def boom(role, **_kw):
        raise RuntimeError("模型角色 'main' 没有可用模型")

    monkeypatch.setattr("backend.infra.llm.proxy.bind_llm_for_role", boom)
    assert svc.classify_intent_llm("随便", has_itinerary=True) is None


# ─────────────────────────────────────────────────────────────
# 3. eval judge 治理化与租户隔离
# ─────────────────────────────────────────────────────────────


def test_judge_uses_eval_gen_role(monkeypatch):
    """judge 走 eval_gen 角色治理出口，不再用 get_llm() 生产裸实例。"""
    from backend.evaluation import judge as judge_mod

    captured: dict = {}

    def fake_bind(role, **_kw):
        captured["role"] = role
        return SimpleNamespace(invoke=lambda messages: SimpleNamespace(
            content='{"scores": {"completeness": 4, "faithfulness": 5, '
                    '"conciseness": 4, "citation_quality": 3}, '
                    '"total": 4.0, "reasoning": "ok", "confidence": "medium"}',
            usage_metadata={"input_tokens": 20, "output_tokens": 8}))

    monkeypatch.setattr("backend.infra.llm.proxy.bind_llm_for_role", fake_bind)
    from backend.evaluation.generation import get_evaluator_token_usage
    before = get_evaluator_token_usage()["llm_calls"]

    result = judge_mod.judge_answer("q", {"completeness": "x"}, "answer")
    assert captured["role"] == "eval_gen"
    assert result.total == 4.0
    # evaluator 内存统计不退化
    assert get_evaluator_token_usage()["llm_calls"] == before + 1


def test_judge_unconfigured_eval_gen_fails_explicitly(monkeypatch):
    """eval_gen 未绑定 → RuntimeError → judge_answer total=0.0（明确失败，
    不误判为通过；与既有异常契约一致）。"""
    from backend.evaluation import judge as judge_mod

    def boom(role, **_kw):
        raise RuntimeError("模型角色 'eval_gen' 没有可用模型")

    monkeypatch.setattr("backend.infra.llm.proxy.bind_llm_for_role", boom)
    result = judge_mod.judge_answer("q", {"completeness": "x"}, "answer")
    assert result.total == 0.0
    assert "failed" in result.reasoning.lower()


def test_eval_tenant_isolated_from_production(monkeypatch):
    """账本守卫、实际调用身份与 Usage Attribution 共用评测租户。"""
    from backend.core.request_context import scoped_tool_identity
    from backend.evaluation import run_guards
    from backend.evaluation.service import EVAL_TENANT_ID
    from backend.infra.llm import budget as budget_mod

    monkeypatch.setattr(budget_mod, "_config_mode", lambda: "observe")
    assert EVAL_TENANT_ID == run_guards.EVAL_TENANT_ID == "eval"
    assert run_guards.BUDGET_TENANT_ID == EVAL_TENANT_ID

    with scoped_tool_identity(user_id="eval-runner", tenant_id=EVAL_TENANT_ID):
        from backend.observability.llm_usage_store import (
            current_usage_attribution,
        )

        attr = current_usage_attribution()
        assert attr["tenant_id"] == run_guards.BUDGET_TENANT_ID
        assert attr["user_id"] == "eval-runner"




def _prepare_stubbed_evaluation(monkeypatch, run_id, evaluate_cases):
    from types import SimpleNamespace

    from backend.evaluation import run_guards, service as service_mod
    from backend.evaluation.config import EvalConfig
    from backend.evaluation import storage, validity
    from backend.infra.llm import budget as budget_mod
    import backend.infra.llm.quota as quota_mod

    quota_queries = []

    class _QuotaStore:
        def get_budget_status(self, *, user_id, tenant_id):
            quota_queries.append((user_id, tenant_id))
            return {"blocked": False, "windows": []}

    monkeypatch.setattr(quota_mod, "PostgresQuotaStore", _QuotaStore)
    monkeypatch.setattr(budget_mod, "_config_mode", lambda: "observe")
    monkeypatch.setattr(
        budget_mod, "_config_limits", lambda: budget_mod.RequestBudgetLimits(),
    )
    monkeypatch.setattr(service_mod, "_current_actor", lambda: "eval-user")
    monkeypatch.setattr(run_guards, "BUDGET_BLOCK_ENABLED", False)
    monkeypatch.setattr(storage, "make_run_id", lambda: run_id)
    monkeypatch.setattr(storage, "mark_run_status", lambda *a, **k: None)
    monkeypatch.setattr(
        storage, "read_run_status",
        lambda _run_id: {"status": "running", "attempt_no": 1},
    )
    monkeypatch.setattr(storage, "is_cancel_requested", lambda _run_id: False)
    monkeypatch.setattr(storage, "touch_run_heartbeat", lambda _run_id: None)
    monkeypatch.setattr(storage, "HEARTBEAT_INTERVAL_SECONDS", 3600)
    monkeypatch.setattr(
        validity,
        "classify_report_validity",
        lambda _report: SimpleNamespace(as_dict=lambda: {"status": "valid"}),
    )

    service = service_mod.EvaluationService.__new__(
        service_mod.EvaluationService,
    )
    monkeypatch.setattr(service, "_ensure_runners", lambda: None)
    monkeypatch.setattr(service, "_evaluate_cases", evaluate_cases)
    return service, EvalConfig(module="planner", run_id=run_id), quota_queries


def test_eval_hard_budget_gate_and_judge_thread_share_eval_context(monkeypatch):
    import threading
    from types import SimpleNamespace

    from backend.core.request_context import scoped_tool_identity
    from backend.evaluation import run_guards
    from backend.evaluation.run_guards import run_judge
    from backend.infra.llm import budget as budget_mod
    from backend.observability.llm_usage_store import current_usage_attribution

    monkeypatch.setattr(budget_mod, "_config_mode", lambda: "observe")
    captured = []

    def evaluate_cases(_config, run_id, _started_at):
        def inspect_worker():
            attr = current_usage_attribution()
            state = budget_mod.current_request_budget()
            return (
                attr["tenant_id"],
                attr["user_id"],
                state.tenant_id,
                state.user_id,
                budget_mod._current_request_id.get(),
                threading.get_ident(),
            )

        captured.append(run_judge(inspect_worker))
        return SimpleNamespace(metadata={})

    service, config, quota_queries = _prepare_stubbed_evaluation(
        monkeypatch, "eval-hard-gate", evaluate_cases,
    )
    monkeypatch.setattr(run_guards, "BUDGET_BLOCK_ENABLED", True)
    with scoped_tool_identity(user_id="caller", tenant_id="tenant-prod"):
        with budget_mod.scoped_request_budget(
            "caller:request", user_id="caller", tenant_id="tenant-prod",
        ):
            report = service._evaluate(config)
            caller_budget = budget_mod.current_request_budget()
            assert caller_budget is not None
            assert caller_budget.tenant_id == "tenant-prod"
            assert current_usage_attribution()["tenant_id"] == "tenant-prod"
            assert current_usage_attribution()["user_id"] == "caller"

    assert report.metadata["run_guards"]["budget_tenant_id"] == "eval"
    assert quota_queries == [("eval-user", "eval")]
    assert captured[0][:5] == (
        "eval", "eval-user", "eval", "eval-user", "eval:eval-hard-gate",
    )


@pytest.mark.parametrize("cancelled", [False, True], ids=["exception", "cancel"])
def test_eval_context_restores_after_exception_or_cancellation(
    monkeypatch, cancelled,
):
    import asyncio

    from backend.core.request_context import scoped_tool_identity
    from backend.infra.llm import budget as budget_mod
    from backend.observability.llm_usage_store import current_usage_attribution

    error = asyncio.CancelledError() if cancelled else RuntimeError("eval failed")

    def evaluate_cases(_config, _run_id, _started_at):
        assert current_usage_attribution()["tenant_id"] == "eval"
        assert budget_mod.current_request_budget().tenant_id == "eval"
        raise error

    service, config, _ = _prepare_stubbed_evaluation(
        monkeypatch, f"eval-{cancelled}", evaluate_cases,
    )
    with scoped_tool_identity(user_id="caller", tenant_id="tenant-prod"):
        with budget_mod.scoped_request_budget(
            "caller:request", user_id="caller", tenant_id="tenant-prod",
        ):
            expected = asyncio.CancelledError if cancelled else RuntimeError
            with pytest.raises(expected):
                service._evaluate(config)
            assert current_usage_attribution()["tenant_id"] == "tenant-prod"
            assert current_usage_attribution()["user_id"] == "caller"
            assert budget_mod.current_request_budget().tenant_id == "tenant-prod"


def test_consecutive_evaluations_and_following_request_do_not_leak(monkeypatch):
    import threading
    from types import SimpleNamespace

    from backend.core.request_context import scoped_tool_identity
    from backend.evaluation.run_guards import run_judge
    from backend.infra.llm import budget as budget_mod
    from backend.observability.llm_usage_store import current_usage_attribution

    monkeypatch.setattr(budget_mod, "_config_mode", lambda: "observe")
    observations = []

    def evaluate_cases(_config, run_id, _started_at):
        def inspect_worker():
            attr = current_usage_attribution()
            state = budget_mod.current_request_budget()
            return (
                attr["tenant_id"], attr["user_id"],
                state.request_id if hasattr(state, "request_id") else
                budget_mod._current_request_id.get(),
                threading.get_ident(),
            )

        observations.append(run_judge(inspect_worker))
        return SimpleNamespace(metadata={})

    with scoped_tool_identity(user_id="caller", tenant_id="tenant-prod"):
        with budget_mod.scoped_request_budget(
            "caller:request", user_id="caller", tenant_id="tenant-prod",
        ):
            for run_id in ("eval-consecutive-1", "eval-consecutive-2"):
                service, config, _ = _prepare_stubbed_evaluation(
                    monkeypatch, run_id, evaluate_cases,
                )
                service._evaluate(config)
                assert current_usage_attribution()["tenant_id"] == "tenant-prod"
                assert current_usage_attribution()["user_id"] == "caller"
                assert budget_mod.current_request_budget().tenant_id == "tenant-prod"

    assert observations[0][:2] == observations[1][:2] == ("eval", "eval-user")
    assert observations[0][2] == "eval:eval-consecutive-1"
    assert observations[1][2] == "eval:eval-consecutive-2"
    assert observations[0][3] == observations[1][3]


def test_budget_settlement_and_release_match_usage_attribution(monkeypatch):
    from decimal import Decimal
    from types import SimpleNamespace

    from backend.core.request_context import scoped_tool_identity
    from backend.infra.llm import budget as budget_mod, pricing
    from backend.infra.llm.pricing import BillingResult
    from backend.infra.llm.resolved_model import (
        ResolvedModelContext,
        reset_current_resolved_model,
        set_current_resolved_model,
    )
    from backend.observability import llm_usage_store

    class _QuotaStore:
        def __init__(self):
            self.reserved = []
            self.settled = []
            self.released = []

        def reserve(self, **kwargs):
            reservation = f"reservation-{len(self.reserved) + 1}"
            self.reserved.append((reservation, kwargs))
            return reservation

        def settle(self, reservation, amount):
            self.settled.append((reservation, amount))

        def release(self, reservation):
            self.released.append(reservation)

        def mark_needs_review(self, *_args, **_kwargs):
            return True

    quota = _QuotaStore()
    usage_rows = []

    class _UsageStore:
        def record(self, event):
            usage_rows.append(event)
            return True

    class _PriceTable:
        def require(self, *_args, **_kwargs):
            return {}

    billing = BillingResult(
        model_name="eval-model",
        component="llm",
        input_tokens=10,
        billable_input_tokens=10,
        output_tokens=5,
        native_cost=Decimal("0.01"),
        native_currency="USD",
        billed_cost_cny=Decimal("0.12"),
        input_cost_cny=Decimal("0.08"),
        output_cost_cny=Decimal("0.04"),
        price_version="synthetic-test-price",
        pricing_source="approved_table",
        usage_source="provider",
        cost_status="exact",
    )
    monkeypatch.setattr(budget_mod, "_config_mode", lambda: "enforce")
    monkeypatch.setattr(
        budget_mod, "_config_limits",
        lambda: budget_mod.RequestBudgetLimits(max_cost=Decimal("1.00")),
    )
    monkeypatch.setattr(
        "backend.infra.llm.quota.PostgresQuotaStore", lambda: quota,
    )
    monkeypatch.setattr(pricing, "get_current_price_table", lambda *_: _PriceTable())
    monkeypatch.setattr(pricing, "price_usage", lambda *_a, **_k: billing)
    monkeypatch.setattr(proxy_mod, "canonical_model_id", lambda name: name)
    monkeypatch.setattr(proxy_mod, "_get_provider_for", lambda _name: "eval-provider")
    monkeypatch.setattr(
        llm_usage_store, "get_llm_usage_store", lambda: _UsageStore(),
    )
    set_current_resolved_model(ResolvedModelContext(
        model_id="eval-model", provider="eval-provider", role="eval_gen",
    ))
    try:
        with scoped_tool_identity(user_id="eval-user", tenant_id="eval"):
            with budget_mod.scoped_request_budget(
                "eval:billing", user_id="eval-user", tenant_id="eval",
            ):
                state = budget_mod.current_request_budget()
                budget_mod.reserve_model_call("primary", model_name="eval-model")
                assert quota.reserved[0][1]["tenant_id"] == "eval"

                proxy_mod._record_tokens(
                    SimpleNamespace(
                        usage_metadata={"input_tokens": 10, "output_tokens": 5},
                        response_metadata={},
                    ),
                    model_name="eval-model",
                )

                row = usage_rows[0]
                assert row["tenant_id"] == "eval"
                assert row["user_id"] == "eval-user"
                assert row["model"] == "eval-model"
                assert row["provider"] == "eval-provider"
                assert row["total_tokens"] == 15
                assert row["billed_cost_cny"] == 0.12
                assert quota.settled == [("reservation-1", Decimal("0.12"))]
                snapshot = state.snapshot()
                assert snapshot.total_tokens == 15
                assert snapshot.cost == Decimal("0.120000")

                # 预占后未向 Provider 发出请求：释放额度且不生成 llm_usage 行。
                budget_mod.reserve_model_call("primary", model_name="eval-model")
                budget_mod.release_model_reservation()
                assert quota.released == ["reservation-2"]
                assert len(usage_rows) == 1
    finally:
        reset_current_resolved_model()


def test_role_resilience_records_bound_model_and_role(monkeypatch):
    from backend.infra.llm.resolved_model import (
        ResolvedModelContext,
        reset_current_resolved_model,
        set_current_resolved_model,
    )

    observed = {}

    class _Breaker:
        def call(self, fn, *args, **kwargs):
            return fn(*args, **kwargs)

    monkeypatch.setattr(proxy_mod, "LLM_MAX_RETRIES", 0)
    monkeypatch.setattr(proxy_mod, "get_active_model_name", lambda: "prod-model")
    monkeypatch.setattr(
        proxy_mod, "_breaker_for_resolved_model",
        lambda model, role, provider: observed.update(
            breaker=(model, role, provider),
        ) or _Breaker(),
    )
    monkeypatch.setattr(proxy_mod, "_is_transient", lambda _exc: False)
    monkeypatch.setattr(
        proxy_mod, "_record_failed_attempt",
        lambda model, _exc, **kw: observed.update(failed=(model, kw)),
    )
    monkeypatch.setattr(
        proxy_mod, "_handle_terminal_failure",
        lambda _exc, _args, _kwargs, **kw: observed.update(handler=kw)
        or "fallback-result",
    )
    set_current_resolved_model(ResolvedModelContext(
        model_id="eval-model", provider="eval-provider", role="eval_gen",
    ))
    try:
        result = proxy_mod._call_with_resilience(
            lambda: (_ for _ in ()).throw(RuntimeError("synthetic failure")),
        )
    finally:
        reset_current_resolved_model()

    assert result == "fallback-result"
    assert observed["breaker"] == ("eval-model", "eval_gen", "eval-provider")
    assert observed["failed"][0] == "eval-model"
    assert observed["handler"] == {
        "model_name": "eval-model", "role": "eval_gen",
    }


def test_fallback_context_preserves_eval_role(monkeypatch):
    from backend.infra.llm.resolved_model import (
        ResolvedModelContext,
        get_current_resolved_model,
        reset_current_resolved_model,
        set_current_resolved_model,
    )

    captured = {}

    class _Fallback:
        def invoke(self, *_args, **_kwargs):
            captured["context"] = get_current_resolved_model()
            return SimpleNamespace(content="ok")

    monkeypatch.setattr(proxy_mod, "_get_fallback_llm", lambda: _Fallback())
    monkeypatch.setattr(proxy_mod, "_configured_fallback_model", lambda: "backup-model")
    monkeypatch.setattr(proxy_mod, "_get_provider_for", lambda _name: "backup-provider")
    monkeypatch.setattr(proxy_mod, "model_identity_extras", lambda _name: {})
    monkeypatch.setattr(
        "backend.infra.llm.budget.reserve_model_call", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(proxy_mod, "_notify_degradation", lambda *_a, **_k: None)
    set_current_resolved_model(ResolvedModelContext(
        model_id="eval-model", provider="eval-provider", role="eval_gen",
    ))
    try:
        result = proxy_mod._handle_terminal_failure(
            RuntimeError("synthetic failure"), (), {},
            model_name="eval-model", role="eval_gen",
        )
    finally:
        reset_current_resolved_model()

    assert result.content == "ok"
    assert captured["context"].model_id == "backup-model"
    assert captured["context"].role == "eval_gen"
    assert captured["context"].binding_source == "fallback"


def test_eval_model_breaker_is_separate_from_production(monkeypatch):
    from backend.infra.circuit_breaker import State, llm_circuit_breaker
    from backend.infra.llm.proxy import _breaker_for_resolved_model

    breaker = _breaker_for_resolved_model(
        "eval-model", "eval_gen", "eval-provider",
    )
    assert breaker is not llm_circuit_breaker
    assert breaker is _breaker_for_resolved_model(
        "eval-model", "eval_gen", "eval-provider",
    )
    monkeypatch.setattr(breaker, "_shared_enabled", lambda: False)
    monkeypatch.setattr(breaker, "_redis_clear_remote", lambda: None)
    production_state = llm_circuit_breaker.state
    breaker.fail_threshold = 1
    breaker.reset()
    with pytest.raises(RuntimeError, match="synthetic provider failure"):
        breaker.call(
            lambda: (_ for _ in ()).throw(
                RuntimeError("synthetic provider failure"),
            ),
        )
    assert breaker.state == State.OPEN
    assert llm_circuit_breaker.state == production_state
    breaker.reset()



def test_async_provider_cancellation_marks_reservation_for_review(monkeypatch):
    import asyncio

    from backend.infra.llm.resolved_model import (
        ResolvedModelContext,
        reset_current_resolved_model,
        set_current_resolved_model,
    )

    calls = []

    class _Breaker:
        async def acall(self, fn, *args, **kwargs):
            return await fn(*args, **kwargs)

    async def _cancelled_provider():
        raise asyncio.CancelledError()

    monkeypatch.setattr(proxy_mod, "LLM_MAX_RETRIES", 0)
    monkeypatch.setattr(
        proxy_mod, "_breaker_for_resolved_model", lambda *_: _Breaker(),
    )
    monkeypatch.setattr(
        "backend.infra.llm.budget.reserve_model_call",
        lambda *a, **k: calls.append("reserve"),
    )
    monkeypatch.setattr(
        "backend.infra.llm.budget.review_model_reservation",
        lambda reason: calls.append(("review", reason)),
    )
    monkeypatch.setattr(
        "backend.infra.llm.budget.release_model_reservation",
        lambda: calls.append("release"),
    )
    set_current_resolved_model(ResolvedModelContext(
        model_id="eval-model", provider="eval-provider", role="eval_gen",
    ))
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(proxy_mod._acall_with_resilience(_cancelled_provider))
    finally:
        reset_current_resolved_model()

    assert calls == [
        "reserve", ("review", "call_cancelled_possibly_billed"),
    ]


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
def test_multi_query_cancellation_after_reserve_releases_before_provider(
    monkeypatch, is_async,
):
    """配额预占慢于 deadline 时，返回后释放预占且不启动 Provider。"""
    import asyncio
    import threading

    from backend.infra.llm.resolved_model import (
        ResolvedModelContext,
        reset_current_resolved_model,
        set_current_resolved_model,
    )

    calls = []
    cancel_event = threading.Event()

    class _Breaker:
        def call(self, function, *args, **kwargs):
            calls.append("provider")
            return function(*args, **kwargs)

        async def acall(self, function, *args, **kwargs):
            calls.append("provider")
            result = function(*args, **kwargs)
            return await result if hasattr(result, "__await__") else result

    def _reserve(*_args, **_kwargs):
        calls.append("reserve")
        cancel_event.set()

    monkeypatch.setattr(
        proxy_mod, "_breaker_for_resolved_model", lambda *_args: _Breaker(),
    )
    monkeypatch.setattr(proxy_mod, "_role_max_retries", lambda _role: 0)
    monkeypatch.setattr(
        "backend.infra.llm.budget.reserve_model_call", _reserve,
    )
    monkeypatch.setattr(
        "backend.infra.llm.budget.release_model_reservation",
        lambda: calls.append("release"),
    )

    set_current_resolved_model(ResolvedModelContext(
        model_id="multi-query-test",
        provider="test-provider",
        role="multi_query",
    ))
    cancellation_token = proxy_mod._role_cancellation_event.set(cancel_event)
    try:
        if is_async:
            with pytest.raises(proxy_mod._RoleCallCancelled):
                asyncio.run(proxy_mod._acall_with_resilience(lambda: "never"))
        else:
            with pytest.raises(proxy_mod._RoleCallCancelled):
                proxy_mod._call_with_resilience(lambda: "never")
    finally:
        proxy_mod._role_cancellation_event.reset(cancellation_token)
        reset_current_resolved_model()

    assert calls == ["reserve", "release"]


@pytest.mark.parametrize(
    ("failure", "expected_action"),
    [(ValueError("bad request"), "release"),
     (TimeoutError("provider timeout"), "review")],
    ids=["known-unbilled", "possibly-billed"],
)
def test_failed_fallback_releases_or_reviews_reservation(
    monkeypatch, failure, expected_action,
):
    from backend.infra.llm.error_taxonomy import ModelProviderError
    from backend.infra.llm.resolved_model import (
        ResolvedModelContext,
        reset_current_resolved_model,
        set_current_resolved_model,
    )

    calls = []

    class _Fallback:
        def invoke(self, *_args, **_kwargs):
            raise failure

    monkeypatch.setattr(proxy_mod, "_get_fallback_llm", lambda: _Fallback())
    monkeypatch.setattr(
        proxy_mod, "_configured_fallback_model", lambda: "backup-model",
    )
    monkeypatch.setattr(proxy_mod, "_get_provider_for", lambda _name: "backup-provider")
    monkeypatch.setattr(proxy_mod, "model_identity_extras", lambda _name: {})
    monkeypatch.setattr(proxy_mod, "LLM_ALLOW_DEGRADED_ANSWER", False)
    monkeypatch.setattr(
        proxy_mod, "_is_transient", lambda error: isinstance(error, TimeoutError),
    )
    monkeypatch.setattr(
        proxy_mod, "_record_failed_attempt", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "backend.infra.llm.budget.reserve_model_call",
        lambda *_a, **_k: calls.append("reserve"),
    )
    monkeypatch.setattr(
        "backend.infra.llm.budget.release_model_reservation",
        lambda: calls.append("release"),
    )
    monkeypatch.setattr(
        "backend.infra.llm.budget.review_model_reservation",
        lambda _reason: calls.append("review"),
    )
    set_current_resolved_model(ResolvedModelContext(
        model_id="eval-model", provider="eval-provider", role="eval_gen",
    ))
    try:
        with pytest.raises(ModelProviderError, match="模型调用失败"):
            proxy_mod._handle_terminal_failure(
                RuntimeError("primary failed"), (), {},
                model_name="eval-model", role="eval_gen",
            )
    finally:
        reset_current_resolved_model()

    assert calls == ["reserve", expected_action]
