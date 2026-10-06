"""test_failure_semantics.py — STOP A/B：Workflow 失败语义收口回归

2026-10-07 全项目 Tool Failure Semantics 收口：
- STOP A：SkillAdapter 不再把 BaseSkill 失败吞成 {}（假成功）；
  成败只依据结构化 status，空业务输出不误判为失败；
  失败信息（tool_status/criticality/error_code）跨层保留。
- STOP B：WorkflowExecutor on_error 三策略语义分明——skip=partial+留痕、
  agent_degrade=真实降级输出（非改名 skip）、abort=failed；
  partial 不再被 trace 抹平成 success。

禁止错误修法对照（任务 §28）：不按 output 是否为空判成败、不 mock 掉
治理层刷绿（集成用例走真实 SafeToolExecutor 失败映射）。
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch as mp

import pytest

from backend.orchestration.workflow import step, workflow
from backend.orchestration.workflow.executor import WorkflowExecutor
from backend.orchestration.workflow.skill_adapter import (
    SkillStepFailure,
    call_skill,
)


# ── 假 Skill：直接返回 BaseSkill 治理路径的 step_results 形态 ──────────

def _failed_sr(**overrides) -> dict:
    sr = {
        "status": "failed",
        "output": None,
        "error": "服务暂时不可用",
        "error_type": "network",
        "tool_status": "unavailable",
        "criticality": "important",
        "error_code": "connect_error",
        "retries": 1,
    }
    sr.update(overrides)
    return {"step_results": {"rag": sr}}


class _FakeFailedSkill:
    """status=failed 的假 RAGSkill（替换 backend.skills.rag.skill.RAGSkill）"""

    name = "rag"

    async def execute(self, state, step_capability="", **kwargs):
        return _failed_sr()


class _FakeSkippedSkill:
    """status=skipped 的假 RAGSkill（BaseSkill OPTIONAL criticality 失败形态）"""

    name = "rag"

    async def execute(self, state, step_capability="", **kwargs):
        return _failed_sr(
            status="skipped",
            error="服务暂时不可用（非关键步骤已跳过）",
            criticality="optional",
        )


class _FakeEmptySuccessSkill:
    """status=success 且业务输出为空列表的假 RAGSkill（合法空结果）"""

    name = "rag"

    async def execute(self, state, step_capability="", **kwargs):
        return {"step_results": {"rag": {"status": "success", "output": []}}}


# ─────────────────────────────────────────────────────────────
# STOP A1：失败 status 必须保留，不得静默返回 {}
# ─────────────────────────────────────────────────────────────

class TestSkillAdapterFailureSemantics:

    def test_workflow_skill_adapter_preserves_failed_status(self):
        """A1/A2/A3/A4：失败上抛且 tool_status/criticality/error_code 保留"""
        async def run():
            with mp("backend.skills.rag.skill.RAGSkill", _FakeFailedSkill):
                with pytest.raises(SkillStepFailure) as ei:
                    await call_skill("rag", "rag.search", {"question": "x"})
            exc = ei.value
            assert exc.tool_status == "unavailable"
            assert exc.criticality == "important"
            assert exc.error_code == "connect_error"
            assert exc.step_result["status"] == "failed"
            assert "服务暂时不可用" in str(exc)
            return exc

        asyncio.run(run())

    def test_workflow_skill_adapter_empty_success_not_failure(self):
        """A6：空业务输出 [] / {} 且 status=success 不是失败——原样返回"""
        async def run():
            with mp("backend.skills.rag.skill.RAGSkill", _FakeEmptySuccessSkill):
                result = await call_skill("rag", "rag.search", {"question": "x"})
            assert result == [], "空列表是合法成功结果，不得改写或抛错"

        asyncio.run(run())

    def test_adapter_skipped_status_not_fake_success(self):
        """OPTIONAL 失败（status=skipped）也不得静默成功——交 on_error 裁决"""
        async def run():
            with mp("backend.skills.rag.skill.RAGSkill", _FakeSkippedSkill):
                with pytest.raises(SkillStepFailure) as ei:
                    await call_skill("rag", "rag.search", {"question": "x"})
            assert ei.value.criticality == "optional"

        asyncio.run(run())

    def test_adapter_contract_violation_fail_loud(self):
        """返回里没有 status（契约破坏）→ fail-loud，不伪装成功"""
        class _NoStatusSkill:
            name = "rag"

            async def execute(self, state, step_capability="", **kwargs):
                return {"step_results": {"rag": {"output": "看起来像结果"}}}

        async def run():
            with mp("backend.skills.rag.skill.RAGSkill", _NoStatusSkill):
                with pytest.raises(SkillStepFailure):
                    await call_skill("rag", "rag.search", {"question": "x"})

        asyncio.run(run())

    def test_skill_adapter_failure_is_value_error(self):
        """SkillStepFailure 是 ValueError 子类：既有 except ValueError 捕获方契约不破坏"""
        assert issubclass(SkillStepFailure, ValueError)

    def test_governed_failure_carries_tool_status_end_to_end(self):
        """集成：真实 BaseSkill 治理路径 + SafeToolExecutor 失败映射——
        连接异常 → ToolStatus.UNAVAILABLE → 适配器异常携带同源 tool_status。
        （不 mock 治理层；能力名避开治理 spec 走 executor 直连分支）"""
        from backend.skills.base import BaseSkill

        class _BrokenTool:
            name = "fake_broken_tool"

            def invoke(self, params):
                raise ConnectionError("connection refused")

        class _RealFailSkill(BaseSkill):
            name = "rag"
            capabilities: list = []

            @property
            def _tool_fn(self):
                return _BrokenTool()

        from backend.core.tool_runtime.policy import ToolPolicy
        import backend.core.tool_runtime.policy as policy_mod

        async def run():
            with mp("backend.skills.rag.skill.RAGSkill", _RealFailSkill), \
                 mp.object(policy_mod, "get_policy",
                           lambda cap: ToolPolicy(circuit_breaker=False,
                                                  retries=0, bulkhead_wait_ms=0)):
                with pytest.raises(SkillStepFailure) as ei:
                    await call_skill("rag", "rag.probe_governed_fail", {"q": "x"})
            assert ei.value.tool_status == "unavailable"
            assert ei.value.step_result["status"] == "failed"

        asyncio.run(run())


# ─────────────────────────────────────────────────────────────
# STOP B：WorkflowExecutor failure policy
# ─────────────────────────────────────────────────────────────

def _raise_adapter_failure(criticality: str = "important", status: str = "failed"):
    """构造一个抛 SkillStepFailure 的 call_skill 替身（模拟 BaseSkill 失败贯通）"""
    from backend.shared import logger as _logger  # noqa: F401  确保包可用

    async def _call_skill(name, cap, params):
        sr = {
            "status": status,
            "output": None,
            "error": "RAG 服务暂时不可用",
            "tool_status": "unavailable",
            "criticality": criticality,
            "error_code": "connect_error",
        }
        raise SkillStepFailure(f"{name}:{cap} 执行失败: {sr['error']}", step_result=sr)

    return _call_skill


class TestWorkflowFailurePolicy:

    def _workflow_executor(self, fresh_registry, cls):
        fresh_registry.register(cls)
        return WorkflowExecutor(registry=fresh_registry)

    def test_optional_tool_failure_workflow_partial(self, fresh_registry, patched_trace_collector, patched_persistence, monkeypatch):
        """B1/B4：optional step 失败（on_error=skip）→ workflow 继续，partial 而非 failed/success"""
        monkeypatch.setattr(
            "backend.orchestration.workflow.skill_adapter.call_skill",
            _raise_adapter_failure(),
        )

        @workflow(name="t_opt_fail")
        class T:
            @step(on_error="skip")
            async def rag_lookup(self, ctx):
                from backend.orchestration.workflow.skill_adapter import call_skill
                return await call_skill("rag", "rag.search", {})

            @step(depends_on=["rag_lookup"])
            async def finalize(self, ctx):
                return {"done": True, "rag": ctx.outputs.get("rag_lookup", {}) or {}}

        ctx = asyncio.run(self._workflow_executor(fresh_registry, T).run("t_opt_fail"))
        assert ctx.status == "partial", "optional 失败 → partial（不能 success 也不能 failed）"
        assert "rag_lookup" in ctx.skip_steps
        assert "finalize" in ctx.outputs and ctx.outputs["finalize"]["done"] is True
        # 失败留痕可辨识（不只是集合名）
        assert ctx.step_failures["rag_lookup"]["tool_status"] == "unavailable"
        assert ctx.step_failures["rag_lookup"]["criticality"] == "important"
        # trace root span 如实标 degraded，不抹平成 success
        # （root span 在所有 step span 之后收口 → 取最后一次 end_span）
        root_call = patched_trace_collector.end_span.call_args_list[-1]
        assert root_call.kwargs.get("status") == "degraded"

    def test_required_tool_failure_workflow_abort(self, fresh_registry, patched_trace_collector, patched_persistence, monkeypatch):
        """B2：required step 失败（on_error=abort 默认）→ workflow failed，下游不执行"""
        monkeypatch.setattr(
            "backend.orchestration.workflow.skill_adapter.call_skill",
            _raise_adapter_failure(),
        )

        @workflow(name="t_req_fail")
        class T:
            @step()
            async def fetch_data(self, ctx):
                from backend.orchestration.workflow.skill_adapter import call_skill
                return await call_skill("sql", "sql.query", {})

            @step(depends_on=["fetch_data"])
            async def after(self, ctx):
                return {"should": "not run"}

        ctx = asyncio.run(self._workflow_executor(fresh_registry, T).run("t_req_fail"))
        assert ctx.status == "failed"
        assert "after" not in ctx.outputs
        assert ctx.step_failures["fetch_data"]["tool_status"] == "unavailable"

    def test_agent_degrade_not_silent_skip(self, fresh_registry, patched_trace_collector, patched_persistence, monkeypatch):
        """B3：agent_degrade 必须产出结构化 degraded 输出（不是改名 skip）——
        下游拿得到 degraded 标记，partial 判定生效"""
        monkeypatch.setattr(
            "backend.orchestration.workflow.skill_adapter.call_skill",
            _raise_adapter_failure(),
        )

        @workflow(name="t_degrade")
        class T:
            @step(on_error="agent_degrade")
            async def analysis(self, ctx):
                from backend.orchestration.workflow.skill_adapter import call_skill
                return await call_skill("rag", "rag.search", {})

            @step(depends_on=["analysis"])
            async def report(self, ctx):
                degraded = ctx.outputs.get("analysis", {})
                return {"analysis_degraded": degraded.get("status") == "degraded",
                        "warning": degraded.get("warning", "")}

        ctx = asyncio.run(self._workflow_executor(fresh_registry, T).run("t_degrade"))
        assert ctx.status == "partial", "降级继续 ≠ 全绿 success"
        assert "analysis" in ctx.degraded_steps
        out = ctx.outputs["analysis"]
        assert out["status"] == "degraded"
        assert out["warning"], "degraded 输出必须携带用户可读的降级说明"
        assert out["data"] is None
        assert ctx.outputs["report"]["analysis_degraded"] is True
        assert ctx.step_failures["analysis"]["error_code"] == "connect_error"
        root_call = patched_trace_collector.end_span.call_args_list[-1]
        assert root_call.kwargs.get("status") == "degraded"
        metrics = root_call.kwargs.get("metrics") or {}
        assert metrics.get("degraded_count") == 1

    def test_all_success_no_false_partial(self, fresh_registry, patched_trace_collector, patched_persistence):
        """B5 反向：全绿 workflow 仍是 success（run_if 跳过不算 partial）"""

        @workflow(name="t_all_ok")
        class T:
            @step()
            async def a(self, ctx):
                return {"x": 1}

            @step(depends_on=["a"], run_if=lambda outputs: False)
            async def b(self, ctx):
                return {"y": 2}

        ctx = asyncio.run(self._workflow_executor(fresh_registry, T).run("t_all_ok"))
        assert ctx.status == "success"
        assert ctx.step_failures == {}
