# -*- coding: utf-8 -*-
"""BaseSkill 错误分类与参数契约测试。

P1: classify_error 把 Tool 错误归类为结构化类型，重试决策与
    sr["error_type"] 留痕共用同一份语义。
P2a: execute() 在 Tool 调用前按 params_schema 校验入参，校验失败
    属 invalid_param（不可重试），直接落 failed 不空转重试。
"""
import asyncio

import pytest

from backend.skills.base import BaseSkill, _CompatSkill, classify_error


def _state(step_id="step_1", params=None):
    return {
        "current_step_id": step_id,
        "step_results": {},
        "plan": {"nodes": {step_id: {"capability": "dummy.cap",
                                     "description": "测试步骤",
                                     "params": params or {}}},
                 "edges": {}},
    }


class _CountingTool:
    """记录被调用次数，可配置抛错。"""
    calls = 0
    error: Exception | None = None
    seen_params: dict | None = None

    def invoke(self, params):
        type(self).calls += 1
        type(self).seen_params = dict(params)
        if self.error:
            raise self.error
        return "ok"


def _run(tool, schema=None, params=None):
    skill = _CompatSkill(tool)
    if schema is not None:
        skill.params_schema = schema
    return asyncio.run(skill.execute(_state(params=params), step_capability="dummy.cap"))


class TestClassifyError:
    @pytest.mark.parametrize("error,expected", [
        ("步骤执行超时（60s）", "timeout"),
        ("query timed out", "timeout"),
        ("权限不足，拒绝访问", "permission"),
        ("permission denied for table orders", "permission"),
        ("no such table: orders", "not_found"),
        ("SQL syntax error near FROM", "syntax"),
        ("column not found: amount", "invalid_param"),
        ("参数校验失败: 缺少必填参数 url", "invalid_param"),
        ("connection refused", "unknown"),
    ])
    def test_classification(self, error, expected):
        assert classify_error(error) == expected

    def test_unretryable_types_stop_retry(self):
        """not_found 类错误第 1 次即停，不空转 3 次"""
        tool = _CountingTool()
        tool.error = RuntimeError("no such table: orders")
        out = _run(tool)
        assert out["step_results"]["step_1"]["status"] == "failed"
        assert out["step_results"]["step_1"]["error_type"] == "not_found"
        assert tool.calls == 1

    def test_unknown_error_retries(self):
        """unknown 类错误可重试直至耗尽"""
        tool = _CountingTool()
        tool.error = RuntimeError("connection refused")
        out = _run(tool)
        assert out["step_results"]["step_1"]["error_type"] == "unknown"
        assert tool.calls == 3  # max_retries=2 + 首次


class TestParamValidation:
    SCHEMA = {
        "url": {"type": "string", "required": True, "description": "目标地址"},
        "count": {"type": "int", "required": False},
        "mode": {"type": "string", "enum": ["fast", "slow"], "required": False},
    }

    def test_missing_required_fails_fast(self):
        """缺必填参数：Tool 一次都不被调用，直接落 failed"""
        tool = _CountingTool()
        out = _run(tool, schema=self.SCHEMA, params={"count": 1})
        sr = out["step_results"]["step_1"]
        assert sr["status"] == "failed"
        assert sr["error_type"] == "invalid_param"
        assert "缺少必填参数 url" in sr["error"]
        assert tool.calls == 0

    def test_wrong_type_rejected(self):
        tool = _CountingTool()
        out = _run(tool, schema=self.SCHEMA,
                   params={"url": "https://x.com", "count": "很多"})
        sr = out["step_results"]["step_1"]
        assert sr["status"] == "failed"
        assert sr["error_type"] == "invalid_param"
        assert "类型应为 int" in sr["error"]
        assert tool.calls == 0

    def test_bool_rejected_for_int(self):
        """bool 是 int 子类，声明 int 时布尔值应判类型错误"""
        tool = _CountingTool()
        out = _run(tool, schema=self.SCHEMA,
                   params={"url": "https://x.com", "count": True})
        assert out["step_results"]["step_1"]["error_type"] == "invalid_param"

    def test_enum_violation_rejected(self):
        tool = _CountingTool()
        out = _run(tool, schema=self.SCHEMA,
                   params={"url": "https://x.com", "mode": "turbo"})
        sr = out["step_results"]["step_1"]
        assert sr["error_type"] == "invalid_param"
        assert "不在允许范围" in sr["error"]

    def test_valid_params_pass_and_reach_tool(self):
        tool = _CountingTool()
        out = _run(tool, schema=self.SCHEMA,
                   params={"url": "https://x.com", "count": 3, "mode": "fast"})
        assert out["step_results"]["step_1"]["status"] == "success"
        assert tool.calls == 1
        assert tool.seen_params == {"url": "https://x.com", "count": 3, "mode": "fast"}

    def test_undeclared_keys_not_blocked(self):
        """未声明的键不拦（交由 Tool 签名兜底），旧式字符串声明跳过"""
        tool = _CountingTool()
        out = _run(tool, schema={"extra": "自由参数（旧式声明）"},
                   params={"extra": "任意值", "other": 1})
        assert out["step_results"]["step_1"]["status"] == "success"


class TestSelectToolHook:
    """默认 _select_tool：原样返回 (_tool_fn, params)"""

    def test_default_passthrough(self):
        tool = _CountingTool()
        skill = _CompatSkill(tool)
        params = {"question": "q"}
        fn, p = skill._select_tool("dummy.cap", params)
        assert fn is tool
        assert p is params
