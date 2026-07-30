"""workflow package — Workflow 引擎（Phase 1）

模块结构：
- meta.py         WorkflowMeta / StepConfig dataclass
- decorator.py    @workflow / @step 装饰器
- registry.py     WorkflowRegistry + Router Index (Phase 1 commit 2)
- dag.py          DAG 拓扑分层 (Phase 1 commit 3)
- executor.py     Async Executor (Phase 1 commit 4)
- context.py      WorkflowContext (Phase 1 commit 4)
- router.py       Task Router (Phase 1 commit 5)
- persistence.py  workflow_runs 持久化 (Phase 1 commit 8)

公开 API：
- @workflow, @step: 装饰器（commit 1）
- WorkflowRegistry: 注册中心（commit 2+）
- WorkflowExecutor: 执行器（commit 4+）
"""
from backend.orchestration.workflow.meta import (
    WorkflowMeta,
    StepConfig,
    get_workflow_meta,
    get_step_config,
    collect_step_methods,
)
from backend.orchestration.workflow.decorator import workflow, step

__all__ = [
    "WorkflowMeta",
    "StepConfig",
    "get_workflow_meta",
    "get_step_config",
    "collect_step_methods",
    "workflow",
    "step",
]