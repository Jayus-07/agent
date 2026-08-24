"""run_if 条件跳过 — 框架扩展测试"""
import asyncio

from backend.orchestration.workflow.decorator import workflow, step
from backend.orchestration.workflow.executor import WorkflowExecutor
from backend.orchestration.workflow.registry import WorkflowRegistry


@workflow(name="t_run_if", description="run_if 测试")
class _RunIfWf:
    @step()
    async def gate(self, ctx):
        return {"v": ctx.inputs.get("gate", "go")}

    @step(depends_on=["gate"], run_if=lambda out: out["gate"]["v"] == "go")
    async def on_go(self, ctx):
        return {"ran": True}

    @step(depends_on=["gate"], run_if=lambda out: out["gate"]["v"] == "no")
    async def on_no(self, ctx):
        return {"ran": True}


def _run(inputs: dict):
    reg = WorkflowRegistry()
    reg.register(_RunIfWf)
    ctx = asyncio.run(WorkflowExecutor(registry=reg).run("t_run_if", inputs=inputs))
    return ctx


def test_run_if_true_runs_step():
    ctx = _run({"gate": "go"})
    assert ctx.outputs["on_go"] == {"ran": True}
    assert "on_go" not in ctx.skip_steps


def test_run_if_false_skips_step_and_records_output():
    ctx = _run({"gate": "go"})
    assert "on_no" in ctx.skip_steps
    assert ctx.outputs["on_no"]["skipped"] is True
    assert "run_if" in ctx.outputs["on_no"]["reason"]


def test_conditional_skip_keeps_success_status():
    """run_if 条件跳过不产生 partial 状态（条件分支是正常路径）"""
    ctx = _run({"gate": "go"})  # on_no 被 run_if 跳过
    assert "on_no" in ctx.skip_steps
    assert ctx.status == "success"


def test_run_if_exception_treated_as_skip():
    """run_if 谓词自身抛异常时按跳过处理，不让 workflow 崩溃"""
    @workflow(name="t_run_if_err", description="谓词异常测试")
    class _WF:
        @step()
        async def a(self, ctx):
            return {}

        @step(depends_on=["a"], run_if=lambda out: out["missing_key"])
        async def b(self, ctx):
            return {"ran": True}

    reg = WorkflowRegistry()
    reg.register(_WF)
    ctx = asyncio.run(WorkflowExecutor(registry=reg).run("t_run_if_err"))
    assert "b" in ctx.skip_steps
    assert ctx.status in ("success", "partial")


def test_step_without_run_if_unaffected():
    """无 run_if 的 step 行为不变（回归保护）"""
    ctx = _run({"gate": "no"})
    assert ctx.outputs["gate"] == {"v": "no"}
