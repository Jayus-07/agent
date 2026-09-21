# -*- coding: utf-8 -*-
"""tool_runtime 治理层单测：ErrorMapper / CircuitBreaker / Bulkhead / Deadline / Retry 规则。

对应「企业级 Tool 失败治理」§9（Retry 规则）/§10（熔断）/§11（隔离舱）/§4（Deadline）。
"""
import asyncio
import threading
import time

import pytest

from backend.core.tool_runtime.bulkhead import bulkhead_registry
from backend.core.tool_runtime.circuit_breaker import CircuitState, circuit_registry
from backend.core.tool_runtime.deadline import RequestDeadline
from backend.core.tool_runtime.error_mapper import map_exception
from backend.core.tool_runtime.executor import safe_tool_executor
from backend.core.tool_runtime.models import OperationType, ToolStatus
from backend.core.tool_runtime.policy import ToolPolicy, reset_policy_cache


@pytest.fixture(autouse=True)
def _clean_registries():
    """每个用例独立的熔断/隔离舱状态。"""
    circuit_registry.reset()
    bulkhead_registry.reset()
    reset_policy_cache()
    yield
    circuit_registry.reset()
    bulkhead_registry.reset()
    reset_policy_cache()


# ==================== ErrorMapper：重试规则（§9） ====================

class TestErrorMapper:
    def _map(self, exc):
        c = map_exception(exc)
        return c.status, c.retryable

    def test_400_422_no_retry(self):
        import httpx
        req = httpx.Request("GET", "http://x")
        for code in (400, 422):
            resp = httpx.Response(code, request=req)
            status, retryable = self._map(httpx.HTTPStatusError("bad", request=req, response=resp))
            assert status is ToolStatus.INVALID_REQUEST and retryable is False

    def test_401_403_no_retry(self):
        import httpx
        req = httpx.Request("GET", "http://x")
        for code in (401, 403):
            resp = httpx.Response(code, request=req)
            status, retryable = self._map(httpx.HTTPStatusError("denied", request=req, response=resp))
            assert status is ToolStatus.UNAUTHORIZED and retryable is False

    def test_404_no_retry(self):
        import httpx
        req = httpx.Request("GET", "http://x")
        resp = httpx.Response(404, request=req)
        status, retryable = self._map(httpx.HTTPStatusError("nf", request=req, response=resp))
        assert status is ToolStatus.FAILED and retryable is False

    def test_502_503_retryable(self):
        import httpx
        req = httpx.Request("GET", "http://x")
        for code in (502, 503):
            resp = httpx.Response(code, request=req)
            status, retryable = self._map(httpx.HTTPStatusError("gw", request=req, response=resp))
            assert status is ToolStatus.UNAVAILABLE and retryable is True

    def test_429_rate_limited_with_retry_after(self):
        import httpx
        req = httpx.Request("GET", "http://x")
        resp = httpx.Response(429, request=req, headers={"Retry-After": "2"})
        c = map_exception(httpx.HTTPStatusError("rl", request=req, response=resp))
        assert c.status is ToolStatus.RATE_LIMITED and c.retryable
        assert c.retry_after_ms == 2000

    def test_connect_error_retryable_read_timeout_not(self):
        import httpx
        assert self._map(httpx.ConnectError("connection refused")) == (
            ToolStatus.UNAVAILABLE, True)
        req = httpx.Request("GET", "http://x")
        assert self._map(httpx.ReadTimeout("read timed out", request=req))[1] is False
        assert self._map(httpx.ConnectTimeout("connect timed out", request=req))[1] is True

    def test_python_timeout_error(self):
        status, retryable = self._map(TimeoutError("整体超时"))
        assert status is ToolStatus.TIMEOUT and retryable is False

    def test_permission_error_no_retry(self):
        status, retryable = self._map(PermissionError("权限不足，拒绝访问"))
        assert status is ToolStatus.UNAUTHORIZED and retryable is False

    def test_original_message_kept(self):
        c = map_exception(ValueError("原始堆栈细节 should stay"))
        assert "原始堆栈细节" in c.error_message


# ==================== CircuitBreaker（§10） ====================

class TestCircuitBreaker:
    def _policy(self, **kw):
        # retries=0 + 短退避：单次 run() 不做重试，保证熔断时序可精确断言
        return ToolPolicy(
            retries=0, retry_backoff_ms=10,
            cb_failure_threshold=kw.get("threshold", 2),
            cb_recovery_seconds=kw.get("recovery", 0.2),
            cb_half_open_max=1,
        )

    @pytest.mark.asyncio
    async def test_open_after_threshold_then_fast_fail(self):
        calls = {"n": 0}

        async def fail():
            calls["n"] += 1
            raise ConnectionError("connection refused")

        pol = self._policy(threshold=2)
        for _ in range(2):
            r = await safe_tool_executor.run(tool_key="cb.svc", call=fail, policy=pol)
            assert r.status is ToolStatus.UNAVAILABLE
        assert calls["n"] == 2  # 连续失败 2 次

        # 第 3 次：OPEN → fast fail，不真实访问服务
        r = await safe_tool_executor.run(tool_key="cb.svc", call=fail, policy=pol)
        assert r.status is ToolStatus.UNAVAILABLE
        assert r.error_code == "CIRCUIT_OPEN"
        assert r.fallback_used == "circuit_breaker"
        assert calls["n"] == 2  # 服务没被再打

    @pytest.mark.asyncio
    async def test_half_open_probe_success_closes(self):
        state = {"fail": True}

        async def maybe_fail():
            if state["fail"]:
                raise ConnectionError("connection refused")
            return "ok"

        pol = self._policy(threshold=1, recovery=0.5)
        pol = ToolPolicy(
            cb_failure_threshold=1, cb_recovery_seconds=0.5,
            cb_half_open_max=1, retries=0, retry_backoff_ms=10,
        )
        await safe_tool_executor.run(tool_key="cb2.svc", call=maybe_fail, policy=pol)
        assert circuit_registry.get("cb2.svc").state() is CircuitState.OPEN

        await asyncio.sleep(0.6)  # 冷却 → HALF_OPEN
        state["fail"] = False
        r = await safe_tool_executor.run(tool_key="cb2.svc", call=maybe_fail, policy=pol)
        assert r.status is ToolStatus.SUCCESS
        assert circuit_registry.get("cb2.svc").state() is CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_breaker_isolation_between_tools(self):
        """rag.search 熔断不影响其他 tool（per-tool/service 维度，§10）。"""
        async def fail():
            raise ConnectionError("connection refused")

        async def ok():
            return "fine"

        pol = self._policy(threshold=1)
        await safe_tool_executor.run(tool_key="iso.a", call=fail, policy=pol)
        assert circuit_registry.get("iso.a").state() is CircuitState.OPEN

        r = await safe_tool_executor.run(tool_key="iso.b", call=ok, policy=pol)
        assert r.status is ToolStatus.SUCCESS  # b 完全不受影响


# ==================== Bulkhead（§11） ====================

class TestBulkhead:
    @pytest.mark.asyncio
    async def test_full_bulkhead_fast_fail(self):
        release = threading.Event()

        async def blocked():
            await asyncio.get_event_loop().run_in_executor(
                None, release.wait, 5.0)
            return "done"

        pol = ToolPolicy(bulkhead_limit=1, bulkhead_wait_ms=100, timeout_ms=10_000)
        first = asyncio.ensure_future(
            safe_tool_executor.run(tool_key="bh.svc", call=blocked, policy=pol))
        await asyncio.sleep(0.05)  # 等 first 占住槽位
        t0 = time.monotonic()
        second = await safe_tool_executor.run(
            tool_key="bh.svc", call=blocked, policy=pol)
        elapsed = time.monotonic() - t0
        assert second.status is ToolStatus.UNAVAILABLE
        assert second.error_code == "TOOL_BUSY"
        assert elapsed < 1.0  # 不无限等待，快速失败
        release.set()
        assert (await first).status is ToolStatus.SUCCESS


# ==================== Deadline（§4） ====================

class TestDeadline:
    def test_remaining_and_expiry(self):
        dl = RequestDeadline(total_budget_ms=1000, workflow_budget_ms=800)
        assert dl.remaining_ms() > 900
        assert not dl.is_expired()
        dl._mono_started -= 2.0  # 时间快进 2s
        assert dl.is_expired()
        assert dl.remaining_workflow_ms() < 0

    def test_ensure_budget_raises(self):
        dl = RequestDeadline(total_budget_ms=1000, workflow_budget_ms=800)
        dl._mono_started -= 0.9  # 只剩 ~100ms
        from backend.core.tool_runtime.deadline import BudgetExhausted
        with pytest.raises(BudgetExhausted):
            dl.ensure_budget(500)

    def test_effective_timeout_capped(self):
        dl = RequestDeadline(total_budget_ms=2000, workflow_budget_ms=1500)
        assert dl.effective_timeout_ms(10_000) <= 1500 - 250  # 被预算封顶
        dl._mono_started -= 10  # 过期
        assert dl.effective_timeout_ms(5000) == 0

    @pytest.mark.asyncio
    async def test_insufficient_budget_skips_tool(self):
        """剩余预算装不下一次完整调用 → 不启动 Tool，直接降级（§4 强制要求）。"""
        calls = {"n": 0}

        async def quick():
            calls["n"] += 1
            return "ok"

        dl = RequestDeadline(total_budget_ms=30_000, workflow_budget_ms=30_000)
        dl._mono_started -= 29.5  # 只剩 ~500ms，策略超时 5s 装不下
        r = await safe_tool_executor.run(
            tool_key="dl.svc", call=quick, policy=ToolPolicy(timeout_ms=5_000),
            deadline=dl)
        assert calls["n"] == 0  # Tool 一次都没被调用（不压缩超时硬等）
        assert r.error_code == "DEADLINE_BUDGET_INSUFFICIENT"
        assert r.fallback_used == "deadline_budget"

    @pytest.mark.asyncio
    async def test_budget_fits_tool_runs_normally(self):
        """预算充足时 Deadline 不干预（正常路径零额外延迟）。"""
        async def quick():
            await asyncio.sleep(0.05)
            return "ok"

        dl = RequestDeadline.started_now()  # 默认 30s 预算
        t0 = time.monotonic()
        r = await safe_tool_executor.run(
            tool_key="dl3.svc", call=quick,
            policy=ToolPolicy(timeout_ms=2_000, retries=0, circuit_breaker=False),
            deadline=dl)
        assert r.status is ToolStatus.SUCCESS
        assert time.monotonic() - t0 < 1.0


# ==================== Executor：重试与写操作（§9/§14） ====================

class TestRetryRules:
    @pytest.mark.asyncio
    async def test_connect_error_retries_once_max(self):
        calls = {"n": 0}

        async def fail():
            calls["n"] += 1
            raise ConnectionError("connection refused")

        r = await safe_tool_executor.run(
            tool_key="rt.svc", call=fail,
            policy=ToolPolicy(retries=1, retry_backoff_ms=10, circuit_breaker=False))
        assert calls["n"] == 2  # 首次 + 1 次快速重试
        assert r.status is ToolStatus.UNAVAILABLE
        assert r.retry_count == 1

    @pytest.mark.asyncio
    async def test_business_error_no_retry(self):
        calls = {"n": 0}

        async def fail():
            calls["n"] += 1
            raise ValueError("column not found: amount")

        r = await safe_tool_executor.run(
            tool_key="rt2.svc", call=fail,
            policy=ToolPolicy(retries=3, retry_backoff_ms=10, circuit_breaker=False))
        assert calls["n"] == 1  # 400/422/404/业务校验失败 → 不 retry
        assert r.status is ToolStatus.FAILED

    @pytest.mark.asyncio
    async def test_write_timeout_never_retries_and_flags_verification(self):
        """写操作 timeout：禁止盲重试 + 标记结果未知（§14）。"""
        calls = {"n": 0}

        async def slow_write():
            calls["n"] += 1
            await asyncio.sleep(1.0)
            return "done"

        r = await safe_tool_executor.run(
            tool_key="write.svc", call=slow_write,
            policy=ToolPolicy(timeout_ms=100, retries=5, circuit_breaker=False,
                              operation_type=OperationType.WRITE))
        assert calls["n"] == 1  # 绝不盲重试
        assert r.status is ToolStatus.TIMEOUT
        assert r.fallback_used == "check_operation_status"

    @pytest.mark.asyncio
    async def test_success_passthrough(self):
        async def ok():
            return {"rows": 3}

        r = await safe_tool_executor.run(
            tool_key="rt3.svc", call=ok,
            policy=ToolPolicy(retries=1, circuit_breaker=False))
        assert r.status is ToolStatus.SUCCESS
        assert r.data == {"rows": 3}
        assert r.retry_count == 0
