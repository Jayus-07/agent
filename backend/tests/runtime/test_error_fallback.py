# -*- coding: utf-8 -*-
"""STOP D：统一异常归一化、重试、熔断、隔离舱与写操作降级。"""

import asyncio

import httpx
import pytest

from backend.core.tool_runtime.bulkhead import bulkhead_registry
from backend.core.tool_runtime.circuit_breaker import circuit_registry
from backend.core.tool_runtime.error_mapper import map_exception
from backend.core.tool_runtime.executor import safe_tool_executor
from backend.core.tool_runtime.models import OperationType, ToolStatus
from backend.core.tool_runtime.policy import ToolPolicy


def _http_error(status: int, retry_after: str | None = None):
    request = httpx.Request("GET", "http://runtime.test")
    headers = {"Retry-After": retry_after} if retry_after else None
    return httpx.HTTPStatusError(
        f"http {status}", request=request,
        response=httpx.Response(status, request=request, headers=headers),
    )


@pytest.mark.parametrize(
    ("factory", "status", "retryable"),
    [
        (lambda: httpx.ConnectError("refused"), ToolStatus.UNAVAILABLE, True),
        (lambda: httpx.ConnectTimeout("connect"), ToolStatus.TIMEOUT, True),
        (lambda: httpx.ReadTimeout("read"), ToolStatus.TIMEOUT, False),
        (lambda: _http_error(429, "1"), ToolStatus.RATE_LIMITED, True),
        (lambda: _http_error(502), ToolStatus.UNAVAILABLE, True),
        (lambda: _http_error(503), ToolStatus.UNAVAILABLE, True),
        (lambda: _http_error(400), ToolStatus.INVALID_REQUEST, False),
        (lambda: _http_error(403), ToolStatus.UNAUTHORIZED, False),
        (lambda: _http_error(404), ToolStatus.FAILED, False),
        (lambda: TimeoutError("整体超时"), ToolStatus.TIMEOUT, False),
        (lambda: ValueError("参数校验失败"), ToolStatus.INVALID_REQUEST, False),
        (lambda: ConnectionError("service unavailable"), ToolStatus.UNAVAILABLE, True),
    ],
)
def test_error_mapper_contract(factory, status, retryable):
    classification = map_exception(factory())
    assert classification.status is status
    assert classification.retryable is retryable


@pytest.fixture(autouse=True)
def _reset_tool_registries():
    circuit_registry.reset()
    bulkhead_registry.reset()
    yield
    circuit_registry.reset()
    bulkhead_registry.reset()


@pytest.mark.asyncio
async def test_connect_failure_retries_then_recovers():
    attempts = 0

    async def call():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("connection refused")
        return "ok"

    result = await safe_tool_executor.run(
        tool_key="stopd.retry", call=call,
        policy=ToolPolicy(retries=1, retry_backoff_ms=0, circuit_breaker=False),
    )
    assert result.status is ToolStatus.SUCCESS
    assert result.retry_count == 1
    assert attempts == 2


@pytest.mark.asyncio
async def test_read_timeout_is_not_retried():
    attempts = 0

    async def call():
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("read", request=httpx.Request("GET", "http://x"))

    result = await safe_tool_executor.run(
        tool_key="stopd.read-timeout", call=call,
        policy=ToolPolicy(retries=2, retry_backoff_ms=0, circuit_breaker=False),
    )
    assert result.status is ToolStatus.TIMEOUT
    assert result.retry_count == 0
    assert attempts == 1


@pytest.mark.asyncio
async def test_http_503_exhausts_retry_without_leaking_exception():
    async def call():
        raise _http_error(503)

    result = await safe_tool_executor.run(
        tool_key="stopd.503", call=call,
        policy=ToolPolicy(retries=1, retry_backoff_ms=0, circuit_breaker=False),
    )
    assert result.status is ToolStatus.UNAVAILABLE
    assert result.retry_count == 1
    assert result.user_friendly_message() == "服务暂时不可用"


@pytest.mark.asyncio
async def test_invalid_request_does_not_retry():
    attempts = 0

    async def call():
        nonlocal attempts
        attempts += 1
        raise ValueError("参数 invalid")

    result = await safe_tool_executor.run(
        tool_key="stopd.invalid", call=call,
        policy=ToolPolicy(retries=3, retry_backoff_ms=0, circuit_breaker=False),
    )
    assert result.status is ToolStatus.INVALID_REQUEST
    assert attempts == 1


@pytest.mark.asyncio
async def test_write_timeout_is_marked_operation_status_unknown():
    async def call():
        await asyncio.sleep(0.02)

    result = await safe_tool_executor.run(
        tool_key="stopd.write", call=call,
        policy=ToolPolicy(timeout_ms=1, retries=3, retry_backoff_ms=0,
                          circuit_breaker=False, operation_type=OperationType.WRITE),
    )
    assert result.status is ToolStatus.TIMEOUT
    assert result.retry_count == 0
    assert result.fallback_used == "check_operation_status"


@pytest.mark.asyncio
async def test_circuit_open_fast_fails_after_threshold():
    calls = 0

    async def call():
        nonlocal calls
        calls += 1
        raise ConnectionError("connection refused")

    policy = ToolPolicy(retries=0, circuit_breaker=True, cb_failure_threshold=1,
                        cb_recovery_seconds=10, cb_half_open_max=1)
    first = await safe_tool_executor.run(tool_key="stopd.cb", call=call, policy=policy)
    second = await safe_tool_executor.run(tool_key="stopd.cb", call=call, policy=policy)
    assert first.status is ToolStatus.UNAVAILABLE
    assert second.error_code == "CIRCUIT_OPEN"
    assert second.fallback_used == "circuit_breaker"
    assert calls == 1


@pytest.mark.asyncio
async def test_bulkhead_full_fast_fails():
    release = asyncio.Event()

    async def blocked():
        await release.wait()
        return "ok"

    policy = ToolPolicy(bulkhead_limit=1, bulkhead_wait_ms=1,
                        timeout_ms=1000, circuit_breaker=False)
    first = asyncio.create_task(
        safe_tool_executor.run(tool_key="stopd.bulkhead", call=blocked, policy=policy)
    )
    await asyncio.sleep(0.01)
    second = await safe_tool_executor.run(
        tool_key="stopd.bulkhead", call=blocked, policy=policy
    )
    release.set()
    await first
    assert second.status is ToolStatus.UNAVAILABLE
    assert second.error_code == "TOOL_BUSY"


@pytest.mark.asyncio
async def test_deadline_budget_skips_call():
    from backend.core.tool_runtime.deadline import RequestDeadline

    called = False

    async def call():
        nonlocal called
        called = True
        return "unexpected"

    deadline = RequestDeadline(total_budget_ms=30_000, workflow_budget_ms=30_000)
    deadline._mono_started -= 29.5
    result = await safe_tool_executor.run(
        tool_key="stopd.deadline", call=call,
        policy=ToolPolicy(timeout_ms=5000, circuit_breaker=False), deadline=deadline,
    )
    assert result.fallback_used == "deadline_budget"
    assert called is False
