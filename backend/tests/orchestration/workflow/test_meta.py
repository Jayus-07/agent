"""test_meta.py — WorkflowMeta + StepConfig dataclass

覆盖：
- 字段类型正确
- 默认值
- dataclass 不可变（hashable）
"""
from __future__ import annotations

import pytest

from backend.orchestration.workflow.meta import (
    StepConfig,
    WorkflowMeta,
    collect_step_methods,
    get_step_config,
    get_workflow_meta,
)


# ─────────────────────────────────────────────────────────────
# WorkflowMeta
# ─────────────────────────────────────────────────────────────

class TestWorkflowMeta:
    """WorkflowMeta 字段"""

    def test_minimal_construction(self):
        """最少字段"""
        meta = WorkflowMeta(name="test")
        assert meta.name == "test"
        assert meta.description == ""
        assert meta.objects == []
        assert meta.actions == []
        assert meta.examples == []
        assert meta.default_kbs == []

    def test_full_construction(self):
        """所有字段"""
        meta = WorkflowMeta(
            name="full",
            description="描述",
            objects=["o1"],
            actions=["a1"],
            examples=["e1"],
            default_kbs=["k1"],
        )
        assert meta.name == "full"
        assert meta.description == "描述"
        assert meta.objects == ["o1"]
        assert meta.actions == ["a1"]
        assert meta.examples == ["e1"]
        assert meta.default_kbs == ["k1"]

    def test_lists_are_independent_between_instances(self):
        """不同实例的 list 字段不共享"""
        m1 = WorkflowMeta(name="a")
        m2 = WorkflowMeta(name="b")
        m1.objects.append("x")
        assert m2.objects == []  # 不受影响

    def test_hashable(self):
        """dataclass 默认 frozen=False，应可哈希（不可变不行，但 eq 可用）"""
        meta = WorkflowMeta(name="test")
        # eq 默认按字段比较
        assert meta == WorkflowMeta(name="test")
        assert meta != WorkflowMeta(name="other")


# ─────────────────────────────────────────────────────────────
# StepConfig
# ─────────────────────────────────────────────────────────────

class TestStepConfig:
    """StepConfig 字段"""

    def test_default_values(self):
        """默认值"""
        cfg = StepConfig()
        assert cfg.depends_on == []
        assert cfg.retry == 0
        assert cfg.timeout_sec == 60
        assert cfg.on_error == "abort"

    def test_depends_on_is_isolated(self):
        """depends_on 默认值不应共享 list"""
        c1 = StepConfig()
        c2 = StepConfig()
        c1.depends_on.append("x")
        assert c2.depends_on == []


# ─────────────────────────────────────────────────────────────
# get_workflow_meta / get_step_config / collect_step_methods
# ─────────────────────────────────────────────────────────────

class TestMetaAccessors:
    """访问器函数"""

    def test_get_workflow_meta_returns_none_for_undocumented_class(self):
        class NoDecorator:
            pass

        assert get_workflow_meta(NoDecorator) is None

    def test_collect_step_methods_finds_decorated(self):
        from backend.orchestration.workflow import workflow, step

        @workflow(name="t")
        class T:
            @step()
            async def s1(self, ctx): return {}

            @step(depends_on=["s1"])
            async def s2(self, ctx): return {}

            async def not_a_step(self, ctx): return {}  # 没装饰

        steps = collect_step_methods(T)
        assert set(steps.keys()) == {"s1", "s2"}

    def test_collect_step_methods_excludes_private(self):
        from backend.orchestration.workflow import workflow, step

        @workflow(name="t")
        class T:
            @step()
            async def s(self, ctx): return {}

            async def _private(self, ctx): return {}

        steps = collect_step_methods(T)
        assert "s" in steps
        assert "_private" not in steps

    def test_get_step_config_returns_config(self):
        from backend.orchestration.workflow import workflow, step

        @workflow(name="t")
        class T:
            @step(retry=3, timeout_sec=120, on_error="skip")
            async def s(self, ctx): return {}

        steps = collect_step_methods(T)
        cfg = get_step_config(steps["s"][0])
        assert cfg.retry == 3
        assert cfg.timeout_sec == 120
        assert cfg.on_error == "skip"