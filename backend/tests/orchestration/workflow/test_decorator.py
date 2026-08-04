"""test_decorator.py — @workflow / @step 装饰器

覆盖：
- @workflow 写入 WorkflowMeta 到类属性
- @step 写入 StepConfig 到方法属性
- on_error 校验（仅合法值）
- retry 校验（非负整数）
- timeout_sec 校验（> 0）
"""
from __future__ import annotations

import pytest

from backend.orchestration.workflow import step, workflow
from backend.orchestration.workflow.meta import (
    StepConfig,
    WorkflowMeta,
    collect_step_methods,
    get_step_config,
    get_workflow_meta,
)


# ─────────────────────────────────────────────────────────────
# @workflow 装饰器
# ─────────────────────────────────────────────────────────────

class TestWorkflowDecorator:
    """@workflow"""

    def test_writes_workflow_meta_to_class(self):
        @workflow(
            name="decorator_wf",
            description="test desc",
            objects=["o1", "o2"],
            actions=["a1"],
            examples=["ex1"],
            default_kbs=["k1"],
        )
        class T:
            @step()
            async def s(self, ctx): return {}

        meta = get_workflow_meta(T)
        assert meta is not None
        assert meta.name == "decorator_wf"
        assert meta.description == "test desc"
        assert meta.objects == ["o1", "o2"]
        assert meta.actions == ["a1"]
        assert meta.examples == ["ex1"]
        assert meta.default_kbs == ["k1"]

    def test_decorator_returns_same_class(self):
        """@workflow 不修改类本身，只附加属性"""
        @workflow(name="x")
        class T:
            pass

        # 类对象没变
        assert T.__name__ == "T"
        assert hasattr(T, "_workflow_meta")

    def test_required_name_argument(self):
        """name 是必填位置参数"""
        with pytest.raises(TypeError):
            workflow()  # 缺 name

    def test_default_values_for_optional_fields(self):
        """description / objects / actions / examples / default_kbs 默认空"""
        @workflow(name="min")
        class T:
            pass

        meta = get_workflow_meta(T)
        assert meta.description == ""
        assert meta.objects == []
        assert meta.actions == []
        assert meta.examples == []
        assert meta.default_kbs == []


# ─────────────────────────────────────────────────────────────
# @step 装饰器
# ─────────────────────────────────────────────────────────────

class TestStepDecorator:
    """@step"""

    def test_writes_step_config_to_method(self):
        @workflow(name="t")
        class T:
            @step(retry=2, timeout_sec=30, on_error="skip")
            async def s(self, ctx): return {}

        steps = collect_step_methods(T)
        cfg = get_step_config(steps["s"][0])
        assert cfg is not None
        assert cfg.retry == 2
        assert cfg.timeout_sec == 30
        assert cfg.on_error == "skip"

    def test_default_step_config(self):
        """@step() 无参数时用默认"""
        @workflow(name="t")
        class T:
            @step()
            async def s(self, ctx): return {}

        steps = collect_step_methods(T)
        cfg = get_step_config(steps["s"][0])
        assert cfg.depends_on == []
        assert cfg.retry == 0
        assert cfg.timeout_sec == 60
        assert cfg.on_error == "abort"

    def test_invalid_on_error_raises(self):
        """on_error 必须是合法值"""
        with pytest.raises(ValueError, match="on_error"):
            step(on_error="invalid_strategy")

    def test_valid_on_error_values_accepted(self):
        for valid in ("abort", "skip", "agent_degrade"):
            cfg = step(on_error=valid)
            # 装饰器已 validate，直接取
            assert cfg is not None

    def test_negative_retry_raises(self):
        with pytest.raises(ValueError, match="retry"):
            step(retry=-1)

    def test_zero_timeout_raises(self):
        with pytest.raises(ValueError, match="timeout_sec"):
            step(timeout_sec=0)

    def test_negative_timeout_raises(self):
        with pytest.raises(ValueError, match="timeout_sec"):
            step(timeout_sec=-1)

    def test_decorator_returns_same_function(self):
        """@step 不修改函数本身"""
        async def my_fn(self, ctx):
            return "hello"

        decorated = step()(my_fn)
        assert decorated is my_fn
        assert hasattr(decorated, "_step_config")

    def test_depends_on_as_keyword(self):
        @step(depends_on=["a", "b"])
        async def s(self, ctx): return {}

        cfg = get_step_config(s)
        assert cfg.depends_on == ["a", "b"]