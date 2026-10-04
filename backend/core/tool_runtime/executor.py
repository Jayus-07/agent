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
from backend.shared.logger import logger
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
from backend.core.tool_runtime.tracing import finish_tool_span, start_tool_span

# 每次真实调用前要求的最低余量：有效超时本身 + 这么多毫秒收尾
_BUDGET_MARGIN_MS = 250.0

# 事件回调：(event_name, info dict) —— BaseSkill 用来写 trace span events
EventCallback = Callable[[str, dict], None]


def _with_tool_attribution(func):
    """Tool 执行栈内绑定 tool 归因（M5 / 台账 D5），叠加语义：外层
    skill_id 保持，tool_id 收窄到当前 Tool。"""
    import functools  # 局部导入：模块头部未引入，避免影响既有导入面

    @functools.wraps(func)
    async def wrapper(self, *, tool_key: str, **kwargs):
        from backend.observability.llm_context import llm_attribution_scope

        with llm_attribution_scope(tool_id=tool_key):
            return await func(self, tool_key=tool_key, **kwargs)

    return wrapper


class SafeToolExecutor:
    """无状态执行器（共享全局熔断/隔离舱注册表），可进程内单例复用。"""

    @_with_tool_attribution
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
        tool_name: str = "",
        trace_span: Any | None = None,
        trace_capability: str = "",
        trace_agent: str = "",
        trace_params: dict[str, Any] | None = None,
    ) -> ToolResult:
        """执行一个 Tool 调用并完成全部治理。

        call: 同步或异步可调用（BaseSkill 传 `lambda: asyncio.to_thread(fn.invoke, params)`）。
        policy: None 时按 tool_key 查注册表。
        operation_type: 显式指定时覆盖策略判定（一般不传）。
        tool_name: @tool 函数名（契约 lock 键），指标/Redis 日键用它记账；
            空 = 沿用 tool_key（capability 名，历史口径，管理端按 lock 合并会对不上）。
        """
        pol = policy or get_policy(tool_key)
        owns_trace_span = trace_span is None
        active_trace_span = trace_span or start_tool_span(
            tool_name or tool_key,
            capability=trace_capability or tool_key,
            params=trace_params,
            agent=trace_agent,
        )

        def _finish(result: ToolResult) -> ToolResult:
            if owns_trace_span:
                finish_tool_span(active_trace_span, result)
            return result

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
                record_tool_result(result, domain, tool_name)
                _emit("circuit_open_fast_fail", {"state": cb.state().value})
                return _finish(result)

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
            record_tool_result(result, domain, tool_name)
            _emit("bulkhead_full", {"limit": pol.bulkhead_limit})
            return _finish(result)

        try:
            return await self._run_with_retries(
                tool_key=tool_key, call=call, pol=pol, deadline=deadline,
                domain=domain, cb=cb, is_write=is_write, op_type=op_type,
                started=started, _emit=_emit, tool_name=tool_name,
                finish_trace=_finish,
            )
        finally:
            bulkhead.release()

    # ── 内部：带超时/重试的真实执行循环 ──
    async def _run_with_retries(
        self, *, tool_key: str, call: Callable[[], Any], pol: ToolPolicy,
        deadline: RequestDeadline | None, domain: str, cb, is_write: bool,
        op_type: OperationType, started: float, _emit: EventCallback,
        tool_name: str = "",
        finish_trace: Callable[[ToolResult], ToolResult] | None = None,
    ) -> ToolResult:
        last: ToolResult | None = None

        for attempt in range(pol.retries + 1):
            # ── Deadline 检查（P1 阶段 2 统一判定）──────────────────
            # check_tool_execution 同时保证：
            #   a) 剩余预算 ≥ min_tool_execution（保底不被 selector/规划吃掉）
            #   b) 本次调用有效超时 = min(策略超时, 剩余−预留−margin)
            #     —— 未注册 tool 的类默认 60s 超时不会再触发「预算必不足」
            #     的假拒绝（D8 实测根因），而是被压缩到预算内真实执行
            # 判定不过 → 直接降级，不压缩超时白等一次超时
            if deadline is not None:
                allow, tool_budget_ms, decision, reason = deadline.check_tool_execution(
                    pol.timeout_ms, margin_ms=_BUDGET_MARGIN_MS,
                )
                logger.info(
                    "[Deadline] tool=%s attempt=%d %s",
                    tool_key, attempt + 1,
                    deadline.budget_log_fields(
                        tool_budget_ms=tool_budget_ms,
                        decision=decision, reason=reason,
                    ),
                )
                if not allow:
                    result = ToolResult(
                        status=ToolStatus.UNAVAILABLE, tool_name=tool_key,
                        latency_ms=int((time.monotonic() - started) * 1000),
                        error_code="DEADLINE_BUDGET_INSUFFICIENT",
                        error_message=f"预算判定拒绝（{reason}），跳过 Tool",
                        fallback_used="deadline_budget",
                    )
                    record_tool_result(result, domain, tool_name)
                    _emit("deadline_budget_insufficient",
                          {"remaining_ms": round(deadline.remaining_workflow_ms()),
                           "required_ms": round(pol.timeout_ms),
                           "decision": decision, "reason": reason})
                    return finish_trace(result) if finish_trace else result
                effective_timeout_s = tool_budget_ms / 1000
            else:
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
                record_tool_result(result, domain, tool_name)
                if deadline is not None:
                    logger.info(
                        "[Deadline] tool=%s success %s",
                        tool_key,
                        deadline.budget_log_fields(
                            tool_budget_ms=effective_timeout_s * 1000,
                            tool_elapsed_ms=latency,
                            decision="done", reason="tool_success",
                        ),
                    )
                _emit("attempt_success", {"attempt": attempt + 1, "latency_ms": latency})
                return finish_trace(result) if finish_trace else result

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
        if deadline is not None:
            logger.info(
                "[Deadline] tool=%s exhausted %s",
                tool_key,
                deadline.budget_log_fields(
                    tool_elapsed_ms=last.latency_ms,
                    decision="done", reason=f"final_status:{last.status.value}",
                ),
            )
        # 写操作超时：状态未知（可能已执行成功但响应丢失），禁止重试后必须可辨识
        if is_write and last.status is ToolStatus.TIMEOUT:
            last.fallback_used = "check_operation_status"
            last.error_message = (last.error_message or "") + "；写操作结果未知，请查询操作状态而非重复提交"
        # 失败穷尽也要带契约名：漏传会回退 capability 名，管理端按 lock 合并时
        # 失败永远映射不回清单行（成功记契约名/失败记 capability 名的口径分裂）
        record_tool_result(last, domain, tool_name)
        return finish_trace(last) if finish_trace else last

    @staticmethod
    async def _invoke(call: Callable[[], Any]) -> Any:
        result = call()
        if isinstance(result, Awaitable) or asyncio.iscoroutine(result):
            return await result
        return result


# 进程内单例
safe_tool_executor = SafeToolExecutor()
