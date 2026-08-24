"""workflow/executor.py — Async Executor

设计原则（按企业方案）：
- DAG 拓扑分层 → 每层 asyncio.gather 并行执行
- 每个 step 一个 trace span（复用 rag/tracer.py）
- retry / timeout / on_error 来自 StepConfig（Runtime 字段）
- depends_on 是 DAG 字段，不在 Runtime 关心

执行流程：
1. 创建 ctx + trace root span
2. 从 Registry 拿 step 方法 + StepConfig
3. 构造 DAG → 分层
4. 逐层执行（gather）
5. 每个 step：
   a. 子 span start
   b. 重试循环（最多 retry+1 次）
   c. 超时（asyncio.wait_for）
   d. on_error 处理：abort / skip / agent_degrade
   e. 子 span end
6. ctx 标 success / failed / partial
7. 自动调 persistence.save()（模块顶部 import，便于测试 monkeypatch）
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any, Callable

from backend.orchestration.workflow.meta import StepConfig
from backend.orchestration.workflow.context import WorkflowContext
from backend.orchestration.workflow.dag import DAG
from backend.orchestration.workflow.registry import WorkflowRegistry, get_workflow_registry
from backend.orchestration.workflow.persistence import get_workflow_run_store
from backend.observability.tracer import trace_collector
from backend.shared.logger import logger


class WorkflowExecutor:
    """执行一个 Workflow 实例"""

    def __init__(self, registry: WorkflowRegistry | None = None):
        self.registry = registry or get_workflow_registry()

    async def run(
        self,
        workflow_name: str,
        inputs: dict[str, Any] | None = None,
    ) -> WorkflowContext:
        """执行一个 workflow

        Args:
            workflow_name: workflow 名（@workflow 装饰器声明）
            inputs: 初始输入

        Returns:
            WorkflowContext: 运行结果（含 outputs / status / duration_ms）
        """
        # 1. 创建 ctx
        ctx = WorkflowContext(
            workflow_name=workflow_name,
            run_id=uuid.uuid4().hex[:12],
            inputs=inputs or {},
        )

        # 2. 拿 step 方法 + config
        steps = self.registry.collect_steps(workflow_name)
        if not steps:
            ctx.mark_failed(f"Workflow {workflow_name!r} 没有 step")
            return ctx

        # 3. 构造 DAG
        steps_config = {name: cfg for name, (_, cfg) in steps.items()}
        try:
            dag = DAG(steps_config)
        except Exception as e:
            ctx.mark_failed(f"DAG 构造失败: {e}")
            logger.error(f"[WorkflowExecutor] {workflow_name} DAG 失败: {e}")
            return ctx

        # 4. 初始化 trace（如果 tracer 已启动则复用，否则启动一个新的）
        trace_root_span = None
        trace_record = None  # 保存引用，用于 finish() 持久化
        try:
            from backend.observability.tracer import trace_collector, _current_trace_var

            # 检查是否已有活跃 trace（如 RAG 调用链中的 workflow step）
            trace_record = _current_trace_var.get() or trace_collector._thread_current
            if trace_record is None:
                # 启动一个新的 workflow trace
                trace_record = trace_collector.start(
                    question=f"[Workflow] {workflow_name}",
                    session_id=ctx.run_id,
                    workflow_name=workflow_name,
                )
                ctx.trace_id = trace_record.id
            else:
                ctx.trace_id = trace_record.id

            meta = self.registry.get_meta(workflow_name)
            trace_root_span = trace_collector.start_span(
                f"workflow_run.{workflow_name}",
                parent_id=None,
                name=f"Workflow: {meta.description or workflow_name}",
                type="workflow",
            )
        except Exception as e:
            logger.debug(f"[WorkflowExecutor] trace 初始化失败（可忽略）: {e}")

        logger.info(
            f"[WorkflowExecutor] 开始 workflow: {workflow_name} "
            f"run_id={ctx.run_id} layers={len(dag.layers)}"
        )

        # 5. 逐层执行
        try:
            for layer_idx, layer in enumerate(dag.layers):
                logger.debug(
                    f"[WorkflowExecutor] Layer {layer_idx}: {layer}"
                )
                await asyncio.gather(*[
                    self._run_step(name, steps, ctx, trace_root_span)
                    for name in layer
                ])
            # 全部 step 成功
            if ctx.skip_steps:
                ctx.mark_partial()
            else:
                ctx.mark_success()
        except Exception as e:
            # abort 失败
            ctx.mark_failed(str(e))
            logger.error(
                f"[WorkflowExecutor] {workflow_name} 失败: {e}"
            )

        # 6. 关闭 root span + 持久化 trace 到 SQLite
        try:
            if trace_root_span is not None:
                from backend.observability.tracer import trace_collector
                trace_collector.end_span(
                    trace_root_span,
                    status="success" if ctx.status in ("success", "partial") else "error",
                    metrics={
                        "duration_ms": ctx.duration_ms or 0,
                        "step_count": len(steps),
                        "skip_count": len(ctx.skip_steps),
                    },
                )
                # finish() 将 trace 持久化到 trace_store（SQLite，重启不丢失）
                if trace_record is not None:
                    trace_collector.finish(
                        trace_record,
                        answer=f"Workflow {workflow_name}: {ctx.status} ({len(ctx.outputs)} steps)",
                        total_ms=ctx.duration_ms or 0,
                        model="workflow",
                        provider="executor",
                    )
        except Exception:
            pass

        logger.info(
            f"[WorkflowExecutor] 完成 workflow: {workflow_name} "
            f"status={ctx.status} duration={ctx.duration_ms}ms"
        )

        # 持久化（Phase 1 Commit 8：自动落库）
        try:
            get_workflow_run_store().save(ctx)
        except Exception as e:
            logger.warning(f"[WorkflowExecutor] 持久化失败（不影响 workflow 结果）: {e}")

        return ctx

    async def _run_step(
        self,
        step_name: str,
        steps: dict[str, tuple[Callable, StepConfig]],
        ctx: WorkflowContext,
        parent_span: Any,
    ) -> None:
        """执行单个 step（含 retry + timeout + on_error + trace）"""
        fn, config = steps[step_name]
        last_error: Exception | None = None

        # trace 子 span
        step_span = None
        try:
            from backend.observability.tracer import trace_collector
            display = config.display_name or step_name
            step_span = trace_collector.start_span(
                f"workflow_step.{step_name}",
                parent_id=parent_span.span_id if parent_span is not None else None,
                name=display,
                type="workflow_step",
            )
        except Exception:
            pass

        # run_if 条件跳过：上游输出不满足谓词 → 记录 skipped 输出并收尾 span
        if config.run_if is not None:
            try:
                should_run = bool(config.run_if(ctx.outputs))
            except Exception as e:
                logger.warning(
                    f"[WorkflowExecutor] step {step_name} run_if 求值失败，按跳过处理: {e}"
                )
                should_run = False
            if not should_run:
                ctx.skip_steps.add(step_name)
                ctx.outputs[step_name] = {"skipped": True, "reason": "run_if 条件不满足"}
                if step_span is not None:
                    try:
                        from backend.observability.tracer import trace_collector
                        trace_collector.end_span(
                            step_span, status="skipped",
                            metrics={"reason": "run_if_false"},
                        )
                    except Exception:
                        logger.debug("[P1-10] step span 跳过收尾失败", exc_info=True)
                logger.info(f"[WorkflowExecutor] step {step_name} 被 run_if 跳过")
                return

        # 实例化 workflow class，让 method 拿到 self
        # （这样 step 方法可以是普通 method，写法自然：async def step_a(self, ctx)）
        try:
            workflow_instance = self._workflow_instance
        except AttributeError:
            # 第一次调用时创建实例
            cls = self.registry.get(ctx.workflow_name)
            if cls is None:
                raise RuntimeError(f"workflow {ctx.workflow_name!r} 未注册")
            workflow_instance = cls()
            self._workflow_instance = workflow_instance

        # 重试循环
        for attempt in range(config.retry + 1):
            try:
                # 调用 raw function：fn(workflow_instance, ctx)
                result = await asyncio.wait_for(
                    fn(workflow_instance, ctx),
                    timeout=config.timeout_sec,
                )
                ctx.outputs[step_name] = result
                if step_span is not None:
                    try:
                        from backend.observability.tracer import trace_collector
                        trace_collector.end_span(
                            step_span,
                            status="success",
                            metrics={"attempt": attempt + 1},
                        )
                    except Exception:
                        logger.debug("[P1-10] step span 收尾失败", exc_info=True)
                logger.debug(
                    f"[WorkflowExecutor] step {step_name} 成功 "
                    f"(attempt={attempt + 1})"
                )
                return
            except asyncio.TimeoutError:
                last_error = asyncio.TimeoutError(
                    f"step {step_name!r} 超时 ({config.timeout_sec}s)"
                )
                logger.warning(
                    f"[WorkflowExecutor] step {step_name} 超时 "
                    f"(attempt={attempt + 1})"
                )
            except Exception as e:
                last_error = e
                logger.warning(
                    f"[WorkflowExecutor] step {step_name} 失败 "
                    f"(attempt={attempt + 1}): {e}"
                )

            # 还有重试机会
            if attempt < config.retry:
                continue
            break

        # 重试全部失败 → on_error 处理
        error_msg = str(last_error) if last_error else "unknown error"
        logger.error(
            f"[WorkflowExecutor] step {step_name} 最终失败: {error_msg}"
        )

        if step_span is not None:
            try:
                from backend.observability.tracer import trace_collector
                trace_collector.end_span(
                    step_span,
                    status="error",
                    metrics={"error": error_msg[:200]},
                )
            except Exception:
                logger.debug("[P1-10] step span 错误收尾失败", exc_info=True)

        # on_error 分支
        if config.on_error == "skip":
            ctx.skip_steps.add(step_name)
            logger.info(
                f"[WorkflowExecutor] step {step_name} skip (on_error=skip)"
            )
        elif config.on_error == "agent_degrade":
            # TODO: Phase 5 — 调 LLM 智能兜底
            logger.warning(
                f"[WorkflowExecutor] step {step_name} agent_degrade 暂未实现，降级为 skip"
            )
            ctx.skip_steps.add(step_name)
        else:
            # abort — 抛出异常让上层终止后续 layer
            raise RuntimeError(
                f"Workflow {ctx.workflow_name} step {step_name!r} failed: {error_msg}"
            ) from last_error