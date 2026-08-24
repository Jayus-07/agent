"""workflow/decorator.py — @workflow + @step 装饰器

设计：
- @workflow: 强类型参数，无大 dict；自动写入 WorkflowMeta 到类属性
- @step: 强类型参数；自动写入 StepConfig 到方法属性；运行时由 Executor 读取

用法：
    @workflow(
        name="daily_report",
        description="每日经营日报",
        objects=["daily_report", "sales"],
        actions=["generate", "send"],
        examples=["生成今天的经营日报"],
        default_kbs=["analytics"],
    )
    class DailyReport:
        @step()
        async def fetch_sales(self, ctx): ...

        @step(depends_on=["fetch_sales"], retry=2)
        async def analyze(self, ctx): ...
"""
from __future__ import annotations

from typing import Callable

from backend.orchestration.workflow.meta import (
    WorkflowMeta,
    StepConfig,
    WORKFLOW_META_ATTR,
    STEP_CONFIG_ATTR,
)


def workflow(
    *,
    name: str,
    description: str = "",
    objects: list[str] | None = None,
    actions: list[str] | None = None,
    examples: list[str] | None = None,
    default_kbs: list[str] | None = None,
    category: str = "",
) -> Callable[[type], type]:
    """类装饰器：声明 Workflow 的元数据

    Args:
        name: Workflow 唯一标识（用于 Router Index / API path）
        description: 描述（Dashboard 展示 + Router LLM 兜底用）
        objects: 业务对象词典（路由"业务对象"维度匹配）
        actions: 动作类型（路由"动作"维度匹配）
        examples: 示例问句（embedding 相似度匹配 + LLM few-shot）
        default_kbs: 默认 RAG 检索的 kb_ids

    Example:
        @workflow(name="daily_report", ...)
        class DailyReport:
            @step()
            async def fetch_sales(self, ctx): ...
    """
    def decorator(cls: type) -> type:
        meta = WorkflowMeta(
            name=name,
            description=description,
            objects=list(objects or []),
            actions=list(actions or []),
            examples=list(examples or []),
            default_kbs=list(default_kbs or []),
            category=category,
        )
        setattr(cls, WORKFLOW_META_ATTR, meta)
        # 不修改类本身，只附加属性
        return cls
    return decorator


def step(
    *,
    depends_on: list[str] | None = None,
    retry: int = 0,
    timeout_sec: int = 60,
    on_error: str = "abort",
    name: str = "",
    run_if: Callable | None = None,
) -> Callable[[Callable], Callable]:
    """方法装饰器：声明 Step 的配置

    Args:
        depends_on: DAG 边 — 依赖哪些上游 step 的输出
            - 空 list（默认）= 独立节点，可与其他无依赖 step 并行
            - 例：["fetch_sales", "fetch_inventory"]
        retry: 失败重试次数（Runtime 行为，不影响 DAG）
        timeout_sec: 单次执行超时秒数（Runtime 行为）
        on_error: 失败处理策略
            - "abort"（默认）：整个 Workflow 报错
            - "skip"：跳过本 step，继续后续
            - "agent_degrade"：调 LLM 智能兜底（TODO: Phase 5）
        run_if: 条件执行谓词，签名 (outputs: dict) -> bool；对 ctx.outputs 求值，
            返回 False 则跳过本 step（记录 skipped 输出与 trace）。None = 无条件执行

    Example:
        @step()  # 独立节点
        async def fetch_sales(self, ctx): ...

        @step(depends_on=["fetch_sales"], retry=2, timeout_sec=120)
        async def analyze(self, ctx): ...
    """
    valid_on_error = {"abort", "skip", "agent_degrade"}
    if on_error not in valid_on_error:
        raise ValueError(
            f"on_error must be one of {valid_on_error}, got {on_error!r}"
        )
    if retry < 0:
        raise ValueError(f"retry must be >= 0, got {retry}")
    if timeout_sec <= 0:
        raise ValueError(f"timeout_sec must be > 0, got {timeout_sec}")
    if run_if is not None and not callable(run_if):
        raise ValueError(f"run_if must be callable or None, got {run_if!r}")

    config = StepConfig(
        depends_on=list(depends_on or []),
        retry=retry,
        timeout_sec=timeout_sec,
        on_error=on_error,
        display_name=name,
        run_if=run_if,
    )

    def decorator(fn: Callable) -> Callable:
        setattr(fn, STEP_CONFIG_ATTR, config)
        return fn
    return decorator