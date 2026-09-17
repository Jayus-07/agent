# -*- coding: utf-8 -*-
"""test_llm_usage_component.py — usage 明细 component 归因（2026-09-18）

客服窗口锁域落地后，token 看板需按窗口核算：cs_prefilter 命中的轮次
（trace.tags 含 cs_target）其 LLM 调用 usage 行 component="customer_service"，
其余沿用 PG 列默认 "llm"。归因函数软失败：trace 不可达时回退默认值。
"""
import pytest

from backend.infra.llm.proxy import _usage_component


@pytest.fixture()
def _turn_trace():
    """起一条真实 trace 并在用例后清理（collector 单例，防串扰）。"""
    from backend.observability.tracer import trace_collector

    trace = trace_collector.start("component 归因测试", "s-component-test",
                                  workflow_name="agent")
    yield trace
    try:
        trace_collector.finish(trace, "", 1, "", "")
    except Exception:
        pass


class TestUsageComponent:
    def test_cs_target_tag_attributed_to_customer_service(self, _turn_trace):
        """cs_prefilter 命中轮次（tags.cs_target 已打标）→ customer_service。"""
        _turn_trace.tags["cs_target"] = "cs_knowledge"
        assert _usage_component() == "customer_service"

    def test_plain_trace_defaults_to_llm(self, _turn_trace):
        """主问答/工具链路（无 cs_target）→ 列默认 llm，行为不变。"""
        assert _usage_component() == "llm"

    def test_other_tags_do_not_leak_into_cs_attribution(self, _turn_trace):
        """非 CS 打标（如 cs_variant 残留）不触发误归因。"""
        _turn_trace.tags["cs_variant"] = "treatment"
        assert _usage_component() == "llm"

    def test_survives_collector_failure(self, monkeypatch):
        """软失败：trace_collector 异常时回退默认值（软失败契约）。"""
        from backend.observability import tracer
        class _Boom:
            def current(self):
                raise RuntimeError("collector down")
        monkeypatch.setattr(tracer, "trace_collector", _Boom())
        assert _usage_component() == "llm"
