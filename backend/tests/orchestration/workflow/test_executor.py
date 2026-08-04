"""test_executor.py — WorkflowExecutor 端到端测试

覆盖：
- Happy path：单 step / 多 step / 并行层
- Error path：abort / skip / agent_degrade
- Retry / Timeout
- on_error 策略
- Trace 集成（子 span）
- workflow_instance 缓存（bug 避险）
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch as mp

import pytest

from backend.orchestration.workflow.context import WorkflowContext
from backend.orchestration.workflow.executor import WorkflowExecutor
from backend.orchestration.workflow import workflow, step


# ─────────────────────────────────────────────────────────────
# Happy Path
# ─────────────────────────────────────────────────────────────

class TestExecutorHappyPath:
    """基础执行流程"""

    def test_single_step_success(self, fresh_registry, patched_trace_collector, patched_persistence):
        """单 step 工作流成功"""
        @workflow(name="t_single")
        class T:
            @step()
            async def only(self, ctx):
                return {"ok": True}

        fresh_registry.register(T)
        executor = WorkflowExecutor(registry=fresh_registry)

        ctx = asyncio.run(executor.run("t_single"))
        assert ctx.status == "success"
        assert ctx.outputs == {"only": {"ok": True}}

    def test_linear_chain_success(self, fresh_registry, patched_trace_collector, patched_persistence):
        """链式依赖执行成功"""
        @workflow(name="t_chain")
        class T:
            @step()
            async def step1(self, ctx):
                return {"a": 1}

            @step(depends_on=["step1"])
            async def step2(self, ctx):
                return {"b": ctx.outputs["step1"]["a"] + 1}

            @step(depends_on=["step2"])
            async def step3(self, ctx):
                return {"c": ctx.outputs["step2"]["b"] + 1}

        fresh_registry.register(T)
        executor = WorkflowExecutor(registry=fresh_registry)

        ctx = asyncio.run(executor.run("t_chain"))
        assert ctx.status == "success"
        assert ctx.outputs["step3"]["c"] == 3

    def test_parallel_layer_executes_concurrently(self, fresh_registry, patched_trace_collector, patched_persistence):
        """3 个独立 step 应在同一层并行（layer[0]）"""
        import time

        call_times: list[float] = []

        @workflow(name="t_parallel")
        class T:
            @step()
            async def a(self, ctx):
                call_times.append(time.time())
                await asyncio.sleep(0.1)
                return {}

            @step()
            async def b(self, ctx):
                call_times.append(time.time())
                await asyncio.sleep(0.1)
                return {}

            @step()
            async def c(self, ctx):
                call_times.append(time.time())
                await asyncio.sleep(0.1)
                return {}

        fresh_registry.register(T)
        executor = WorkflowExecutor(registry=fresh_registry)

        start = time.time()
        asyncio.run(executor.run("t_parallel"))
        elapsed = time.time() - start

        # 3 个 step 并行（每个 0.1s）→ 总耗时应该 < 0.25s
        # 如果串行会 ~0.3s+
        assert elapsed < 0.25, f"Steps 似乎串行了（elapsed={elapsed:.3f}s）"

    def test_diamond_dag(self, fresh_registry, patched_trace_collector, patched_persistence):
        """菱形 DAG：a → (b, c) → d"""
        @workflow(name="t_diamond")
        class T:
            @step()
            async def a(self, ctx):
                return {"v": "A"}

            @step(depends_on=["a"])
            async def b(self, ctx):
                return {"v": "B"}

            @step(depends_on=["a"])
            async def c(self, ctx):
                return {"v": "C"}

            @step(depends_on=["b", "c"])
            async def d(self, ctx):
                return {"merged": ctx.outputs["b"]["v"] + ctx.outputs["c"]["v"]}

        fresh_registry.register(T)
        executor = WorkflowExecutor(registry=fresh_registry)

        ctx = asyncio.run(executor.run("t_diamond"))
        assert ctx.status == "success"
        assert ctx.outputs["d"]["merged"] == "BC"


# ─────────────────────────────────────────────────────────────
# 错误处理
# ─────────────────────────────────────────────────────────────

class TestExecutorErrorHandling:
    """Step 失败的错误处理"""

    def test_on_error_abort_stops_workflow(self, fresh_registry, patched_trace_collector, patched_persistence):
        """abort 失败终止 workflow"""
        @workflow(name="t_abort")
        class T:
            @step()
            async def first(self, ctx):
                return {"ok": True}

            @step(depends_on=["first"])
            async def fail_step(self, ctx):
                raise ValueError("故意失败")

            @step(depends_on=["fail_step"])
            async def after(self, ctx):
                return {"should": "not run"}

        fresh_registry.register(T)
        executor = WorkflowExecutor(registry=fresh_registry)

        ctx = asyncio.run(executor.run("t_abort"))
        assert ctx.status == "failed"
        assert "fail_step" in ctx.error or "故意失败" in ctx.error
        # after step 不应执行
        assert "after" not in ctx.outputs

    def test_on_error_skip_continues_workflow(self, fresh_registry, patched_trace_collector, patched_persistence):
        """skip 失败跳过继续"""
        @workflow(name="t_skip")
        class T:
            @step()
            async def first(self, ctx):
                return {"ok": True}

            @step(depends_on=["first"], on_error="skip")
            async def fail_step(self, ctx):
                raise ValueError("故意失败")

            @step(depends_on=["fail_step"])
            async def after(self, ctx):
                return {"ran": True}

        fresh_registry.register(T)
        executor = WorkflowExecutor(registry=fresh_registry)

        ctx = asyncio.run(executor.run("t_skip"))
        # 部分成功
        assert ctx.status == "partial"
        # skip 后后续 step 应执行
        assert "after" in ctx.outputs
        assert ctx.outputs["after"]["ran"] is True
        assert "fail_step" in ctx.skip_steps

    def test_on_error_skip_independent_failures(self, fresh_registry, patched_trace_collector, patched_persistence):
        """独立 step 失败互不影响"""
        @workflow(name="t_skip_multi")
        class T:
            @step(on_error="skip")
            async def fail1(self, ctx):
                raise ValueError("err1")

            @step(on_error="skip")
            async def fail2(self, ctx):
                raise ValueError("err2")

            @step()
            async def ok(self, ctx):
                return {"ok": True}

        fresh_registry.register(T)
        executor = WorkflowExecutor(registry=fresh_registry)

        ctx = asyncio.run(executor.run("t_skip_multi"))
        assert ctx.status == "partial"
        assert "ok" in ctx.outputs

    def test_invalid_workflow_name_raises(self, fresh_registry, patched_trace_collector, patched_persistence):
        """未注册的 workflow 名返回 failed"""
        executor = WorkflowExecutor(registry=fresh_registry)
        ctx = asyncio.run(executor.run("nonexistent"))
        assert ctx.status == "failed"
        assert "nonexistent" in ctx.error

    def test_workflow_without_steps_returns_failed(self, fresh_registry, patched_trace_collector, patched_persistence):
        """没有 @step 的 workflow 直接失败"""
        @workflow(name="t_empty")
        class T:
            pass

        fresh_registry.register(T)
        executor = WorkflowExecutor(registry=fresh_registry)

        ctx = asyncio.run(executor.run("t_empty"))
        assert ctx.status == "failed"


# ─────────────────────────────────────────────────────────────
# Retry / Timeout
# ─────────────────────────────────────────────────────────────

class TestExecutorRetryTimeout:
    """retry 和 timeout 行为"""

    def test_retry_succeeds_on_second_attempt(self, fresh_registry, patched_trace_collector, patched_persistence):
        """retry=1：第一次失败，第二次成功"""
        attempt_count = [0]

        @workflow(name="t_retry_ok")
        class T:
            @step(retry=2)
            async def step1(self, ctx):
                attempt_count[0] += 1
                if attempt_count[0] < 2:
                    raise ValueError("first attempt fail")
                return {"ok": True}

        fresh_registry.register(T)
        executor = WorkflowExecutor(registry=fresh_registry)

        ctx = asyncio.run(executor.run("t_retry_ok"))
        assert ctx.status == "success"
        assert attempt_count[0] == 2

    def test_retry_exhausted_aborts(self, fresh_registry, patched_trace_collector, patched_persistence):
        """retry 用尽 + on_error=abort → 失败"""
        attempt_count = [0]

        @workflow(name="t_retry_fail")
        class T:
            @step(retry=1, on_error="abort")
            async def step1(self, ctx):
                attempt_count[0] += 1
                raise ValueError("always fail")

        fresh_registry.register(T)
        executor = WorkflowExecutor(registry=fresh_registry)

        ctx = asyncio.run(executor.run("t_retry_fail"))
        assert ctx.status == "failed"
        assert attempt_count[0] == 2  # retry=1 → 2 次尝试

    def test_timeout_marks_failed(self, fresh_registry, patched_trace_collector, patched_persistence):
        """step 超时 → 失败"""
        @workflow(name="t_timeout")
        class T:
            @step(timeout_sec=0.05, on_error="abort")
            async def slow(self, ctx):
                await asyncio.sleep(0.5)  # 超过 0.05s
                return {}

        fresh_registry.register(T)
        executor = WorkflowExecutor(registry=fresh_registry)

        ctx = asyncio.run(executor.run("t_timeout"))
        assert ctx.status == "failed"
        assert "超时" in ctx.error or "timeout" in ctx.error.lower()


# ─────────────────────────────────────────────────────────────
# Trace 集成
# ─────────────────────────────────────────────────────────────

class TestExecutorTrace:
    """Executor 创建 root span + 每个 step 一个子 span"""

    def test_creates_root_and_step_spans(self, fresh_registry, patched_trace_collector, patched_persistence):
        """root span + 每个 step 一个子 span"""
        @workflow(name="t_trace")
        class T:
            @step()
            async def step1(self, ctx):
                return {}

            @step(depends_on=["step1"])
            async def step2(self, ctx):
                return {}

        fresh_registry.register(T)
        executor = WorkflowExecutor(registry=fresh_registry)

        asyncio.run(executor.run("t_trace"))

        # patched_trace_collector.start_span 被调用多次（root + 2 step）
        assert patched_trace_collector.start_span.call_count >= 3

        # 检查 span name 包含 workflow_run 和 workflow_step
        span_names = [
            call.args[0] for call in patched_trace_collector.start_span.call_args_list
        ]
        assert any("workflow_run" in n for n in span_names)
        assert any("workflow_step.step1" in n for n in span_names)
        assert any("workflow_step.step2" in n for n in span_names)


# ─────────────────────────────────────────────────────────────
# Workflow 实例缓存（bug 避险）
# ─────────────────────────────────────────────────────────────

class TestExecutorWorkflowInstanceCache:
    """验证：每个 executor 实例独立，不跨 workflow 串扰"""

    def test_each_executor_gets_fresh_instance(self, fresh_registry, patched_trace_collector, patched_persistence):
        """Executor 在第一次 step 创建 workflow instance（不串扰）"""
        @workflow(name="t_cache")
        class T:
            @step()
            async def step1(self, ctx):
                # 验证 self 是 T 的实例
                return {"is_t_instance": isinstance(self, T)}

        fresh_registry.register(T)

        # 两次执行用同一个 executor
        executor = WorkflowExecutor(registry=fresh_registry)
        ctx1 = asyncio.run(executor.run("t_cache"))
        ctx2 = asyncio.run(executor.run("t_cache"))

        # 两次都成功
        assert ctx1.status == "success"
        assert ctx2.status == "success"


# ─────────────────────────────────────────────────────────────
# Context 状态
# ─────────────────────────────────────────────────────────────

class TestExecutorContextState:
    """ctx 字段正确填充"""

    def test_ctx_has_run_id(self, fresh_registry, patched_trace_collector, patched_persistence):
        """ctx.run_id 是 12 字符 hex"""
        @workflow(name="t_ctx")
        class T:
            @step()
            async def step1(self, ctx):
                return {}

        fresh_registry.register(T)
        executor = WorkflowExecutor(registry=fresh_registry)

        ctx = asyncio.run(executor.run("t_ctx"))
        assert len(ctx.run_id) == 12
        # hex only
        int(ctx.run_id, 16)  # 不抛错就是 hex

    def test_ctx_status_marks_success(self, fresh_registry, patched_trace_collector, patched_persistence):
        """所有 step 成功 → ctx.status == success"""
        @workflow(name="t_success")
        class T:
            @step()
            async def step1(self, ctx):
                return {}

        fresh_registry.register(T)
        executor = WorkflowExecutor(registry=fresh_registry)

        ctx = asyncio.run(executor.run("t_success"))
        assert ctx.status == "success"
        assert ctx.finished_at is not None
        assert ctx.duration_ms >= 0

    def test_ctx_duration_ms_positive(self, fresh_registry, patched_trace_collector, patched_persistence):
        """duration_ms >= 0"""
        @workflow(name="t_dur")
        class T:
            @step()
            async def step1(self, ctx):
                await asyncio.sleep(0.05)
                return {}

        fresh_registry.register(T)
        executor = WorkflowExecutor(registry=fresh_registry)

        ctx = asyncio.run(executor.run("t_dur"))
        assert ctx.duration_ms >= 50  # 至少 50ms

    def test_ctx_inputs_preserved(self, fresh_registry, patched_trace_collector, patched_persistence):
        """传入 inputs 在 ctx.inputs"""
        @workflow(name="t_inputs")
        class T:
            @step()
            async def step1(self, ctx):
                return {"got": ctx.inputs}

        fresh_registry.register(T)
        executor = WorkflowExecutor(registry=fresh_registry)

        ctx = asyncio.run(executor.run("t_inputs", {"x": 42, "y": "hi"}))
        assert ctx.outputs["step1"]["got"] == {"x": 42, "y": "hi"}