"""node_runtime/runner.py — 节点公共执行生命周期（六段）

log_start → timer → invoke（可选 TimeoutStrategy）→ exception policy →
result wrap → log_done

定位（docs/architecture/ai-runtime.md「Node Runtime」）：这是专家/域内
节点层的公共生命周期，不是第二套 Tool 治理——retry/breaker/bulkhead 属
core/tool_runtime，专家层唯一的执行关切是 timeout。runner 对业务零感知：
结果包装（域 TypedDict）、遥测形态（CS metrics-only / Travel span）、
异常策略选择全部在调用方与 hooks。

成功状态透传：fn 正常返回 dict 时，其自报 status（result.get("status")，
缺省 "success"）作为 NodeResult.status 与 on_success 的 status 原样传递
（与迁移前 CS/Travel 两个手写实现同语义，含「key 存在但值为 None」的
病理路径行为）。
"""
from __future__ import annotations

import concurrent.futures
import contextvars
import time
from typing import Any, Callable

from backend.core.node_runtime.context import ExecutionContext
from backend.core.node_runtime.error_policy import ErrorPolicy, TimeoutStrategy
from backend.core.node_runtime.hooks import ObservabilityHooks
from backend.core.node_runtime.models import NodeResult, NodeStatus

_NOOP_HOOKS = ObservabilityHooks()


class NodeRunner:
    """专家节点的公共执行生命周期。无状态可共享，一次 run 承载一个调用。"""

    def run(
        self,
        ctx: ExecutionContext,
        fn: Callable[..., Any],
        state: Any,
        *,
        policy: ErrorPolicy = ErrorPolicy.SWALLOW_TO_STATUS,
        hooks: ObservabilityHooks | None = None,
        timeout_strategy: TimeoutStrategy = TimeoutStrategy.NONE,
        fallback: Any = None,
    ) -> NodeResult:
        if timeout_strategy is TimeoutStrategy.THREAD_ISOLATED and not (
            ctx.deadline and ctx.deadline > 0
        ):
            # 快速失败：THREAD_ISOLATED 没有正的限时窗口是调用方配置错误
            raise ValueError(
                f"THREAD_ISOLATED requires a positive deadline, got {ctx.deadline!r}"
            )

        resolved_hooks = hooks if hooks is not None else _NOOP_HOOKS
        t0 = time.monotonic()
        resolved_hooks.on_start(ctx)

        if timeout_strategy is TimeoutStrategy.THREAD_ISOLATED:
            return self._run_thread_isolated(
                ctx, fn, state, t0=t0, policy=policy, hooks=resolved_hooks,
            )

        try:
            result = fn(state)
        except Exception as e:
            return self._apply_policy(
                ctx, e, t0, policy=policy, hooks=resolved_hooks,
                fallback=fallback,
            )
        return self._wrap_success(ctx, result, t0, hooks=resolved_hooks)

    def _run_thread_isolated(
        self, ctx: ExecutionContext, fn: Callable[..., Any], state: Any, *,
        t0: float, policy: ErrorPolicy, hooks: ObservabilityHooks,
    ) -> NodeResult:
        # P2.3：per-call 独立单 worker 池——不用共享池，超时孤儿任务随池
        # shutdown(wait=False) 自生自灭，不占后续调用额度；线程名带节点名
        # 便于线程转储归因。
        # B4：ThreadPoolExecutor.submit 不携带 contextvars（新线程是空上下文），
        # 必须显式拷贝当前上下文再执行（THREAD_ISOLATED 的唯一形态，见
        # error_policy.py 两案号说明）。
        pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"{ctx.domain}-expert-{ctx.node_name}",
        )
        thread_ctx = contextvars.copy_context()
        try:
            future = pool.submit(thread_ctx.run, fn, state)
            try:
                result = future.result(timeout=ctx.deadline)
            except concurrent.futures.TimeoutError:
                duration_ms = int((time.monotonic() - t0) * 1000)
                # 未开跑则取消；已开跑的孤儿线程无法强杀，自行结束
                future.cancel()
                hooks.on_error(ctx, kind="timeout", duration_ms=duration_ms)
                return NodeResult(
                    status=NodeStatus.TIMEOUT.value,
                    error=f"node '{ctx.node_name}' timed out after {ctx.deadline}s",
                    duration_ms=duration_ms,
                )
            except Exception as e:
                return self._apply_policy(ctx, e, t0, policy=policy, hooks=hooks)
        finally:
            pool.shutdown(wait=False)
        return self._wrap_success(ctx, result, t0, hooks=hooks)

    def _apply_policy(
        self, ctx: ExecutionContext, exc: Exception, t0: float, *,
        policy: ErrorPolicy, hooks: ObservabilityHooks, fallback: Any = None,
    ) -> NodeResult:
        duration_ms = int((time.monotonic() - t0) * 1000)
        if policy is ErrorPolicy.RAISE_THROUGH:
            raise exc
        if policy is ErrorPolicy.FALLBACK:
            # 兜底值视作正常产出（status=success）；异常本身仍经 on_error 留痕
            hooks.on_error(ctx, kind="exception", duration_ms=duration_ms, exc=exc)
            return NodeResult(
                status=NodeStatus.SUCCESS.value,
                duration_ms=duration_ms,
                data=fallback,
            )
        hooks.on_error(ctx, kind="exception", duration_ms=duration_ms, exc=exc)
        return NodeResult(
            status=NodeStatus.FAILED.value,
            error=str(exc),
            duration_ms=duration_ms,
        )

    def _wrap_success(
        self, ctx: ExecutionContext, result: Any, t0: float, *,
        hooks: ObservabilityHooks,
    ) -> NodeResult:
        duration_ms = int((time.monotonic() - t0) * 1000)
        status: Any = NodeStatus.SUCCESS.value
        if isinstance(result, dict):
            status = result.get("status", status)
        hooks.on_success(ctx, status=status, duration_ms=duration_ms)
        return NodeResult(status=status, duration_ms=duration_ms, data=result)
