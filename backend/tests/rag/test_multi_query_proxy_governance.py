"""MultiQuery 角色在 LLMProxy 中消费重试与失败策略。"""

import asyncio

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
