"""MultiQuery 角色在 LLMProxy 中消费重试与失败策略。"""

import asyncio
import threading

import pytest


@pytest.mark.parametrize("retry_limit, expected_calls", [(0, 1), (1, 2)])
@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
def test_multi_query_proxy_uses_role_retry_limit_and_skip_policy(
    monkeypatch, retry_limit, expected_calls, is_async,
):
    """角色重试上限生效；skip 失败只回到调用方，不调用备用模型。"""
    import backend.infra.llm.proxy as proxy
    from backend.config.model_roles import RoleRuntimeDefaults
    from backend.infra.llm.error_taxonomy import ModelProviderError

    calls = {"provider": 0, "reserve": [], "review": [], "failed": 0}

    class _Breaker:
        def call(self, fn, *args, **kwargs):
            return fn(*args, **kwargs)

        async def acall(self, fn, *args, **kwargs):
            return await fn(*args, **kwargs)

    def sync_provider():
        calls["provider"] += 1
        raise TimeoutError("safe provider failure")

    async def async_provider():
        calls["provider"] += 1
        raise TimeoutError("safe provider failure")

    monkeypatch.setattr(
        proxy, "_resilience_call_identity",
        lambda: ("mq-test-model", "multi_query", "test-provider"),
    )
    monkeypatch.setattr(proxy, "_breaker_for_resolved_model", lambda *_: _Breaker())
    monkeypatch.setattr(proxy, "LLM_MAX_RETRIES", 5)
    monkeypatch.setattr(proxy, "LLM_RETRY_BACKOFF_BASE", 0)
    monkeypatch.setattr(
        proxy,
        "_role_runtime_policy",
        lambda _role: RoleRuntimeDefaults(
            timeout_seconds=1,
            max_retries=retry_limit,
            failure_policy="skip",
        ),
    )
    monkeypatch.setattr(
        "backend.infra.llm.budget.reserve_model_call",
        lambda kind, **_kwargs: calls["reserve"].append(kind),
    )
    monkeypatch.setattr(
        "backend.infra.llm.budget.review_model_reservation",
        lambda reason: calls["review"].append(reason),
    )
    monkeypatch.setattr(
        "backend.infra.llm.budget.release_model_reservation", lambda: None,
    )
    monkeypatch.setattr(
        proxy, "_record_failed_attempt",
        lambda *_args, **_kwargs: calls.__setitem__("failed", calls["failed"] + 1),
    )
    monkeypatch.setattr(proxy, "_get_fallback_llm", lambda: pytest.fail(
        "MultiQuery failure_policy=skip must not call the fallback model",
    ))
    monkeypatch.setattr(proxy, "_get_provider_for", lambda _model: "test-provider")
    monkeypatch.setattr(proxy, "canonical_model_id", lambda model: model)
    monkeypatch.setattr(proxy, "LLM_ALLOW_DEGRADED_ANSWER", True)

    with pytest.raises(ModelProviderError):
        if is_async:
            asyncio.run(proxy._acall_with_resilience(async_provider))
        else:
            proxy._call_with_resilience(sync_provider)

    assert calls["provider"] == expected_calls
    assert calls["reserve"] == ["primary"] + ["retry"] * retry_limit
    assert calls["review"] == ["call_failed_possibly_billed"] * expected_calls
    assert calls["failed"] == expected_calls

@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
def test_multi_query_timeout_signal_stops_retries_and_skips_fallback(
    monkeypatch, is_async,
):
    """调用方超时后，已运行请求可结算，但不能继续重试或走 fallback。"""
    import backend.infra.llm.proxy as proxy
    from backend.config.model_roles import RoleRuntimeDefaults

    event = threading.Event()
    calls = {"provider": 0, "reserve": [], "review": [], "failed": 0}

    class _Breaker:
        def call(self, fn, *args, **kwargs):
            return fn(*args, **kwargs)

        async def acall(self, fn, *args, **kwargs):
            return await fn(*args, **kwargs)

    def sync_provider():
        calls["provider"] += 1
        event.set()
        raise TimeoutError("provider timed out after caller deadline")

    async def async_provider():
        calls["provider"] += 1
        event.set()
        raise TimeoutError("provider timed out after caller deadline")

    monkeypatch.setattr(
        proxy, "_resilience_call_identity",
        lambda: ("mq-test-model", "multi_query", "test-provider"),
    )
    monkeypatch.setattr(proxy, "_breaker_for_resolved_model", lambda *_: _Breaker())
    monkeypatch.setattr(
        proxy,
        "_role_runtime_policy",
        lambda _role: RoleRuntimeDefaults(
            timeout_seconds=1,
            max_retries=3,
            failure_policy="skip",
        ),
    )
    monkeypatch.setattr(proxy, "LLM_RETRY_BACKOFF_BASE", 1)
    monkeypatch.setattr(
        "backend.infra.llm.budget.reserve_model_call",
        lambda kind, **_kwargs: calls["reserve"].append(kind),
    )
    monkeypatch.setattr(
        "backend.infra.llm.budget.review_model_reservation",
        lambda reason: calls["review"].append(reason),
    )
    monkeypatch.setattr(
        "backend.infra.llm.budget.release_model_reservation", lambda: None,
    )
    monkeypatch.setattr(
        proxy, "_record_failed_attempt",
        lambda *_args, **_kwargs: calls.__setitem__("failed", calls["failed"] + 1),
    )
    monkeypatch.setattr(
        proxy, "_get_fallback_llm",
        lambda: pytest.fail("cancelled MultiQuery must not call fallback"),
    )

    call = proxy._acall_with_resilience if is_async else proxy._call_with_resilience
    provider = async_provider if is_async else sync_provider
    with pytest.raises(TimeoutError, match="deadline expired"):
        if is_async:
            async def invoke():
                return await call(provider)
            proxy.run_with_role_cancellation(event, lambda: asyncio.run(invoke()))
        else:
            proxy.run_with_role_cancellation(event, call, provider)

    assert calls["provider"] == 1
    assert calls["reserve"] == ["primary"]
    assert calls["review"] == ["call_failed_possibly_billed"]
    assert calls["failed"] == 1


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
def test_multi_query_fallback_uses_role_policy_model(monkeypatch, is_async, caplog):
    """角色配置的 fallbackModel 优先于全局 fallback，预占使用同一模型。"""
    import logging

    import backend.infra.llm.proxy as proxy
    from backend.config.model_roles import RoleRuntimeDefaults

    calls = {"built": [], "invoked": 0, "reserved": []}

    class _Fallback:
        def bind(self, **kwargs):
            calls["bound_kwargs"] = kwargs
            return self

        def invoke(self, *_args, **_kwargs):
            calls["invoked"] += 1
            return "rewritten query"

        async def ainvoke(self, *_args, **_kwargs):
            calls["invoked"] += 1
            return "rewritten query"

    fallback = _Fallback()
    monkeypatch.setattr(
        proxy, "_role_runtime_policy",
        lambda _role: RoleRuntimeDefaults(
            timeout_seconds=1,
            max_retries=0,
            failure_policy="fallback",
            fallback_model="role-specific-backup",
        ),
    )
    monkeypatch.setattr(
        proxy, "_get_override_llm",
        lambda model: calls["built"].append(model) or fallback,
    )
    monkeypatch.setattr(
        proxy, "_configured_fallback_model", lambda: "global-backup",
    )
    monkeypatch.setattr(
        proxy, "_get_fallback_llm",
        lambda: pytest.fail("role-specific backup must take precedence"),
    )
    monkeypatch.setattr(
        "backend.infra.llm.budget.reserve_model_call",
        lambda kind, **kwargs: calls["reserved"].append(
            (kind, kwargs.get("model_name"))
        ),
    )
    monkeypatch.setattr(
        "backend.infra.llm.budget.review_model_reservation", lambda *_: None,
    )
    monkeypatch.setattr(
        "backend.infra.llm.budget.release_model_reservation", lambda: None,
    )
    monkeypatch.setattr(proxy, "canonical_model_id", lambda model: model)
    monkeypatch.setattr(proxy, "_get_provider_for", lambda _model: "test-provider")
    monkeypatch.setattr(proxy, "model_identity_extras", lambda _model: {})
    monkeypatch.setattr(proxy, "get_current_resolved_model", lambda: None)
    monkeypatch.setattr(proxy, "set_current_resolved_model", lambda _ctx: None)
    monkeypatch.setattr(proxy, "_notify_degradation", lambda *_args, **_kwargs: None)
    caplog.set_level(logging.INFO)

    primary_error = (
        "api_key=primary-sentinel https://provider.invalid/private"
    )

    token = proxy._role_fallback_generation_kwargs.set(
        {"temperature": 0, "max_tokens": 512})
    try:
        if is_async:
            result = asyncio.run(proxy._ahandle_terminal_failure(
                TimeoutError(primary_error),
                (),
                {},
                model_name="mq-primary",
                role="multi_query",
            ))
        else:
            result = proxy._handle_terminal_failure(
                TimeoutError(primary_error),
                (),
                {},
                model_name="mq-primary",
                role="multi_query",
            )
    finally:
        proxy._role_fallback_generation_kwargs.reset(token)

    assert result == "rewritten query"
    assert calls["built"] == ["role-specific-backup"]
    assert calls["reserved"] == [("fallback", "role-specific-backup")]
    assert calls["bound_kwargs"] == {"temperature": 0, "max_tokens": 512}
    assert calls["invoked"] == 1
    assert "primary-sentinel" not in caplog.text
    assert "provider.invalid" not in caplog.text


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
def test_multi_query_fallback_failure_logs_only_exception_types(
    monkeypatch, is_async, caplog,
):
    """备用失败日志及透传异常不得包含 Provider 返回的敏感文本。"""
    import logging

    import backend.infra.llm.proxy as proxy
    from backend.config.model_roles import RoleRuntimeDefaults

    primary_error = "api_key=primary-sentinel https://provider.invalid/private"
    fallback_error = (
        "password=fallback-sentinel postgresql://user:fake@db.internal/private"
    )

    class _Fallback:
        def invoke(self, *_args, **_kwargs):
            raise RuntimeError(fallback_error)

        async def ainvoke(self, *_args, **_kwargs):
            raise RuntimeError(fallback_error)

    monkeypatch.setattr(
        proxy, "_role_runtime_policy",
        lambda _role: RoleRuntimeDefaults(failure_policy="fallback"),
    )
    monkeypatch.setattr(
        proxy, "_get_role_fallback_llm",
        lambda _role: ("mq-backup", _Fallback()),
    )
    monkeypatch.setattr(
        "backend.infra.llm.budget.reserve_model_call", lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "backend.infra.llm.budget.review_model_reservation", lambda *_args: None,
    )
    monkeypatch.setattr(
        "backend.infra.llm.budget.release_model_reservation", lambda: None,
    )
    monkeypatch.setattr(proxy, "canonical_model_id", lambda model: model)
    monkeypatch.setattr(proxy, "_get_provider_for", lambda _model: "test-provider")
    monkeypatch.setattr(proxy, "model_identity_extras", lambda _model: {})
    monkeypatch.setattr(proxy, "get_current_resolved_model", lambda: None)
    monkeypatch.setattr(proxy, "set_current_resolved_model", lambda _ctx: None)
    monkeypatch.setattr(proxy, "_record_failed_attempt", lambda *_args, **_kwargs: None)
    caplog.set_level(logging.INFO)

    with pytest.raises(Exception) as exc_info:
        if is_async:
            asyncio.run(proxy._ahandle_terminal_failure(
                TimeoutError(primary_error), (), {},
                model_name="mq-primary", role="multi_query",
            ))
        else:
            proxy._handle_terminal_failure(
                TimeoutError(primary_error), (), {},
                model_name="mq-primary", role="multi_query",
            )

    for sensitive_fragment in (
        "primary-sentinel", "fallback-sentinel", "provider.invalid",
        "db.internal", "postgresql://",
    ):
        assert sensitive_fragment not in caplog.text
        assert sensitive_fragment not in str(exc_info.value)
    assert "RuntimeError" in caplog.text


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
def test_bound_llm_proxy_scopes_fallback_generation_kwargs(monkeypatch, is_async):
    """角色参数只在本次治理调用期间可供 fallback 读取。"""
    import backend.infra.llm.proxy as proxy

    observed = []
    expected = {"temperature": 0, "max_tokens": 512}

    class _Bound:
        def invoke(self, *_args, **_kwargs):
            return "ok"

        async def ainvoke(self, *_args, **_kwargs):
            return "ok"

    wrapped = proxy._BoundLLMProxy(
        _Bound(), model_name="mq-primary", role="multi_query",
        generation_kwargs=expected,
    )
    monkeypatch.setattr(proxy, "_enforce_rate_limit", lambda _user_id: None)
    monkeypatch.setattr(proxy._BoundLLMProxy, "_bind_context", lambda _self: None)
    monkeypatch.setattr(
        proxy, "_preflight_context",
        lambda args, _kwargs, **_options: args,
    )

    async def _apreflight(args, _kwargs, **_options):
        return args

    monkeypatch.setattr(proxy, "_apreflight_context", _apreflight)
    monkeypatch.setattr(proxy, "_record_tokens", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(proxy, "_wrap_result", lambda result: result)

    def _call(_attr, *args, **kwargs):
        observed.append(proxy._role_fallback_generation_kwargs.get())
        return "ok"

    async def _acall(_attr, *args, **kwargs):
        observed.append(proxy._role_fallback_generation_kwargs.get())
        return "ok"

    monkeypatch.setattr(proxy, "_call_with_resilience", _call)
    monkeypatch.setattr(proxy, "_acall_with_resilience", _acall)

    if is_async:
        assert asyncio.run(wrapped.ainvoke("query")) == "ok"
    else:
        assert wrapped.invoke("query") == "ok"

    assert observed == [expected]
    assert proxy._role_fallback_generation_kwargs.get() is None


def test_multi_query_unsupported_legacy_policy_uses_default(monkeypatch, caplog):
    """存量不支持策略不绕过写入校验静默改变运行语义。"""
    import logging

    import backend.infra.llm.proxy as proxy
    from backend.config import model_roles

    monkeypatch.setattr(
        model_roles,
        "resolve_runtime_policy",
        lambda _role: model_roles.RoleRuntimeDefaults(
            timeout_seconds=21,
            max_retries=2,
            failure_policy="template_response",
        ),
    )
    caplog.set_level(logging.WARNING)

    policy = proxy._role_runtime_policy("multi_query")

    assert policy.failure_policy == "skip"
    assert policy.timeout_seconds == 21
    assert policy.max_retries == 2
    assert "非支持 failure_policy" in caplog.text
    assert "template_response" not in caplog.text


def test_other_role_is_not_routed_through_multi_query_policy(monkeypatch):
    """MultiQuery 策略读取只作用于该角色。"""
    import backend.infra.llm.proxy as proxy
    from backend.config import model_roles

    monkeypatch.setattr(
        model_roles,
        "resolve_runtime_policy",
        lambda _role: pytest.fail("其他角色不应读取 MultiQuery 策略"),
    )

    assert proxy._role_runtime_policy("ocr") is None
