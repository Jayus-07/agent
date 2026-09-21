"""tool_runtime/executor.py — SafeToolExecutor（统一 Tool 执行层）

调用链：
    Domain / Skill 节点
      → SafeToolExecutor.run(tool_key, call, policy, deadline)
          ├── CircuitBreaker  fast fail（OPEN 时完全不真实访问服务）
          ├── Bulkhead        并发隔离，满 → TOOL_BUSY 快速失败
          ├── Deadline        每次调用前检查剩余预算，不足 → 直接降级
          ├── Timeout         min(策略超时, 剩余预算)，硬封顶
          ├── Retry           按 retry.py 保守决策（写操作恒不重试）
          ├── ErrorMapper     底层异常 → ToolStatus
          └── Metrics / Trace 回调
      → ToolResult（上层只判断 status）

返回值永远是 ToolResult，绝不向上抛业务异常（CancelledError 除外——
那是调用方主动取消，必须传播）。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from backend.core.tool_runtime.bulkhead import bulkhead_registry
from backend.core.tool_runtime.circuit_breaker import circuit_registry
from backend.core.tool_runtime.deadline import RequestDeadline
from backend.core.tool_runtime.error_mapper import map_exception
from backend.core.tool_runtime.metrics import (
    record_circuit_open,
    record_tool_result,
)
from backend.core.tool_runtime.models import (
    OperationType,
    ToolResult,
    ToolStatus,
)
from backend.core.tool_runtime.policy import DEFAULT_POLICY, get_policy
from backend.core.tool_runtime.retry import should_retry, sleep_before_retry

# 每次真实调用前要求的最低余量：有效超时本身 + 这么多毫秒收尾
_BUDGET_MARGIN_MS = 250.0

# 事件回调：(event_name, info dict) —— BaseSkill 用来写 trace span events
EventCallback = Callable[[str, dict], None]


class SafeToolExecutor:
    """无状态执行器（共享全局熔断/隔离舱注册表），可进程内单例复用。"""

    async def run(
        self,
        *,
        tool_key: str,
        call: Callable[[], Any],
        policy: ToolPolicy | None = None,
        deadline: RequestDeadline | None = None,
        domain: str = "",
        on_event: EventCallback | None = None,
        operation_type: OperationType | None = None,
    ) -> ToolResult:
        """执行一个 Tool 调用并完成全部治理。

        call: 同步或异步可调用（BaseSkill 传 `lambda: asyncio.to_thread(fn.invoke, params)`）。
        policy: None 时按 tool_key 查注册表。
        operation_type: 显式指定时覆盖策略判定（一般不传）。
        """
        pol = policy or get_policy(tool_key)
        op_type = operation_type or pol.operation_type
        # 写操作强制零重试（双保险：策略层 for_write 已清零，这里兜底）
        is_write = op_type is OperationType.WRITE
        if is_write:
            pol = pol.for_write()

        domain = domain or tool_key.split(".", 1)[0]

        def _emit(event: str, info: dict | None = None) -> None:
            if on_event is not None:
                try:
                    on_event(event, info or {})
                except Exception:  # pragma: no cover — 观测回调不干扰业务
                    pass

        started = time.monotonic()

        # ── 1. 熔断检查：OPEN → fast fail，不真实访问服务 ──
        cb = None
        if pol.circuit_breaker:
            cb = circuit_registry.get(
                tool_key, pol.cb_failure_threshold, pol.cb_recovery_seconds, pol.cb_half_open_max
            )
            if not cb.allow_request():
                result = ToolResult(
                    status=ToolStatus.UNAVAILABLE, tool_name=tool_key,
                    latency_ms=0, error_code="CIRCUIT_OPEN",
                    error_message="熔断器打开，快速失败",
                    fallback_used="circuit_breaker",
                )
                record_tool_result(result, domain)
                _emit("circuit_open_fast_fail", {"state": cb.state().value})
                return result

        # ── 2. Bulkhead：极短等待，拿不到槽位快速失败 ──
        bulkhead = bulkhead_registry.get(tool_key, pol.bulkhead_limit)
        acquired = await bulkhead.acquire(pol.bulkhead_wait_ms)
        if not acquired:
            result = ToolResult(
                status=ToolStatus.UNAVAILABLE, tool_name=tool_key,
                latency_ms=int((time.monotonic() - started) * 1000),
                error_code="TOOL_BUSY",
                error_message=f"并发隔离舱已满（limit={pol.bulkhead_limit}）",
                fallback_used="bulkhead",
            )
            record_tool_result(result, domain)
            _emit("bulkhead_full", {"limit": pol.bulkhead_limit})
            return result

        try:
            return await self._run_with_retries(
                tool_key=tool_key, call=call, pol=pol, deadline=deadline,
                domain=domain, cb=cb, is_write=is_write, op_type=op_type,
                started=started, _emit=_emit,
            )
        finally:
            bulkhead.release()

    # ── 内部：带超时/重试的真实执行循环 ──
    async def _run_with_retries(
        self, *, tool_key: str, call: Callable[[], Any], pol: ToolPolicy,
        deadline: RequestDeadline | None, domain: str, cb, is_write: bool,
        op_type: OperationType, started: float, _emit: EventCallback,
    ) -> ToolResult:
        last: ToolResult | None = None

        for attempt in range(pol.retries + 1):
            # ── Deadline 检查：剩余预算装不下"一次完整调用"就不启动（§4）──
            # 例：剩余 4s、策略超时 6s → 直接降级，而不是压缩超时白等一次超时
            if deadline is not None:
                remaining = deadline.remaining_workflow_ms()
                if remaining < pol.timeout_ms + _BUDGET_MARGIN_MS:
                    result = ToolResult(
                        status=ToolStatus.UNAVAILABLE, tool_name=tool_key,
                        latency_ms=int((time.monotonic() - started) * 1000),
                        error_code="DEADLINE_BUDGET_INSUFFICIENT",
                        error_message=(
                            f"剩余预算 {remaining:.0f}ms 不足以完成 "
                            f"{pol.timeout_ms:.0f}ms 的调用，跳过 Tool"),
                        fallback_used="deadline_budget",
                    )
                    record_tool_result(result, domain)
                    _emit("deadline_budget_insufficient",
                          {"remaining_ms": round(remaining),
                           "required_ms": round(pol.timeout_ms)})
                    return result

            effective_timeout_s = pol.timeout_ms / 1000

            _emit("attempt_start", {
                "attempt": attempt + 1, "max_attempts": pol.retries + 1,
                "timeout_s": round(effective_timeout_s, 2), "write": is_write,
            })

            attempt_started = time.monotonic()
            try:
                output = await asyncio.wait_for(
                    self._invoke(call), timeout=effective_timeout_s
                )
                latency = int((time.monotonic() - attempt_started) * 1000)
                if cb is not None:
                    cb.record_success()
                result = ToolResult(
                    status=ToolStatus.SUCCESS, tool_name=tool_key,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    data=output, retry_count=attempt,
                )
                record_tool_result(result, domain)
                _emit("attempt_success", {"attempt": attempt + 1, "latency_ms": latency})
                return result

            except asyncio.CancelledError:
                # 调用方主动取消：原样传播，不吞（也不算熔断失败）
                raise

            except Exception as exc:
                cls = map_exception(exc)
                latency = int((time.monotonic() - attempt_started) * 1000)
                if cb is not None and cb.record_failure():
                    record_circuit_open(tool_key)

                result = ToolResult(
                    status=cls.status, tool_name=tool_key,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    error_code=cls.error_code, error_message=cls.error_message,
                    retryable=cls.retryable, retry_count=attempt,
                    original_exception=exc,
                )
                _emit("attempt_failed", {
                    "attempt": attempt + 1, "status": cls.status.value,
                    "error_code": cls.error_code,
                    "error": str(exc)[:120],
                })
                last = result

                decision = should_retry(
                    cls, attempt, pol, deadline,
                    effective_timeout_ms=effective_timeout_s * 1000,
                )
                if not decision.should_retry:
                    _emit("retry_skipped", {"reason": decision.reason})
                    break
                _emit("retry_scheduled", {
                    "attempt": attempt + 1, "delay_ms": round(decision.delay_ms),
                })
                await sleep_before_retry(decision.delay_ms)

        assert last is not None
        # 写操作超时：状态未知（可能已执行成功但响应丢失），禁止重试后必须可辨识
        if is_write and last.status is ToolStatus.TIMEOUT:
            last.fallback_used = "check_operation_status"
            last.error_message = (last.error_message or "") + "；写操作结果未知，请查询操作状态而非重复提交"
        record_tool_result(last, domain)
        return last

    @staticmethod
    async def _invoke(call: Callable[[], Any]) -> Any:
        result = call()
        if isinstance(result, Awaitable) or asyncio.iscoroutine(result):
            return await result
        return result


# 进程内单例
safe_tool_executor = SafeToolExecutor()
