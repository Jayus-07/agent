"""统一 llm_call span 出口测试（2026-10-30 obs-collection-handoff）。

覆盖：
- agent 链路（无调用方 LLM span）出现 llm_call span（根因回归：此前恒为 0）
- 双计防线：调用方已管理 LLM span（kind=llm / type=llm_call）时跳过创建
- span 指标与时间窗回写、prompt 快照提取与复位
- _record_tokens 集成：proxy 结算后自动出 span
- 不双计：span tokens == usage 行 tokens，backfill 覆盖后 trace.usage 不叠加
- 脱敏矩阵：masked/summary 级 prompt_text/response_text 不泄漏原文
"""
from __future__ import annotations

import json

import pytest

from backend.observability import tracer as tracer_mod
from backend.observability.llm_call_span import (
    consume_prompt_snapshot,
    note_prompt_snapshot,
    record_llm_call_span,
)
from backend.tests.test_llm_span_fields import (  # noqa: F401
    _seed_registry_pricing,
    reset_proxy_state,
)

_PRICING_TEST_MODEL = "acceptance-test-chat"


class _FakeResult:
    """类 AIMessage 的最小 fake（content 可读即可）。"""

    def __init__(self, content: str):
        self.content = content


def _fresh_tracer(monkeypatch) -> tracer_mod.TraceCollector:
    """替换全局 trace_collector（llm_call_span 函数内导入，运行时读取生效）。

    不用 PG store：本模块只触达 start_span/end_span 内存路径，不落库。
    """
    collector = tracer_mod.TraceCollector()
    monkeypatch.setattr(tracer_mod, "trace_collector", collector)
    tracer_mod._current_trace_var.set(None)
    return collector


_META = {
    "model": _PRICING_TEST_MODEL,
    "provider": "test-provider",
    "prompt_tokens": 100,
    "completion_tokens": 50,
    "total_tokens": 150,
    "cached_tokens": 0,
    "reasoning_tokens": 0,
    "cost_cny": 0.021,
    "cost_usd": 0.003,
    "finish_reason": "stop",
    "model_role": "main",
    "binding_source": "code_default",
    "usage_source": "provider",
}


# ==========================================================
# 1. agent 链路出现 llm_call span（根因回归）
# ==========================================================

class TestAgentTraceGetsLlmCallSpan:
    def test_span_created_with_metrics(self, monkeypatch):
        collector = _fresh_tracer(monkeypatch)
        trace = collector.start("介绍一下自己", workflow_name="agent")
        collector.start_span("root", parent_id=None, name="Agent 图",
                             type="workflow")

        note_prompt_snapshot((["介绍一下你自己"],))
        record_llm_call_span(
            metrics=dict(_META),
            result=_FakeResult("你好！我是企业智能运营助手。"),
            duration_ms=1234.5,
        )

        llm_spans = [s for s in trace.spans if s.type == "llm_call"]
        assert len(llm_spans) == 1
        span = llm_spans[0]
        assert span.kind == tracer_mod.SpanKind.LLM.value
        assert span.metrics["model_name"] == _PRICING_TEST_MODEL
        assert span.metrics["prompt_tokens"] == 100
        assert span.metrics["completion_tokens"] == 50
        assert span.metrics["total_tokens"] == 150
        assert span.metrics["cost_cny"] == pytest.approx(0.021)
        assert span.metrics["token_source"] == "proxy_span"
        assert span.metrics["duration_ms_call"] == 1234.5
        # 时间窗回写：start < end，跨度≈调用耗时（round 与 end_span 同为
        # banker's rounding：1234.5 → 1234）
        assert span.start_time < span.end_time
        assert span.duration_ms == 1234
        # 文本快照进入 DTO 读取链（input.prompt / output.response）
        assert span.input == {"prompt": "介绍一下你自己"}
        assert span.output == {"response": "你好！我是企业智能运营助手。"}
        # llm_call span 已收口（root 是测试自身保持打开的 span，不在此列）
        assert all(s.type != "llm_call"
                   for s in getattr(trace, "_open_spans", []))

    def test_no_active_trace_is_noop(self, monkeypatch):
        _fresh_tracer(monkeypatch)
        # 不 start trace：必须静默，不抛错
        record_llm_call_span(metrics=dict(_META), duration_ms=10.0)

    def test_degraded_response_has_empty_output(self, monkeypatch):
        """_record_tokens 无 usage 早退路径不会建 span；本用例验证
        result 无 content 时 response 如实留空。"""
        collector = _fresh_tracer(monkeypatch)
        trace = collector.start("q", workflow_name="agent")
        record_llm_call_span(metrics=dict(_META), result=None, duration_ms=5.0)
        llm_spans = [s for s in trace.spans if s.type == "llm_call"]
        assert len(llm_spans) == 1
        assert llm_spans[0].output is None


# ==========================================================
# 2. 双计防线：调用方已管理 LLM span 时跳过
# ==========================================================

class TestDedup:
    def test_skip_when_innermost_span_is_llm_kind(self, monkeypatch):
        """模拟 RAG llm_generate：open span kind=llm，同一调用不再建 span。"""
        collector = _fresh_tracer(monkeypatch)
        trace = collector.start("q", workflow_name="rag_agent")
        collector.start_span("root", parent_id=None, name="RAG", type="workflow")
        collector.start_span("llm_generate", name="LLM生成",
                             kind=tracer_mod.SpanKind.LLM.value)
        before = len(trace.spans)

        record_llm_call_span(metrics=dict(_META), duration_ms=10.0)
        assert len(trace.spans) == before

    def test_skip_when_innermost_span_type_is_llm_call(self, monkeypatch):
        """模拟 self_correction_rewrite / reranker：open span type=llm_call。"""
        collector = _fresh_tracer(monkeypatch)
        trace = collector.start("q", workflow_name="rag_agent")
        collector.start_span("root", parent_id=None, name="RAG", type="workflow")
        collector.start_span("self_correction_rewrite",
                             name="Self-Correction Rewrite")
        before = len(trace.spans)

        record_llm_call_span(metrics=dict(_META), duration_ms=10.0)
        assert len(trace.spans) == before

    def test_normal_span_does_not_block(self, monkeypatch):
        """最内层 open span 是普通工具 span 时不阻塞创建（agent 工具节点内直答）。"""
        collector = _fresh_tracer(monkeypatch)
        trace = collector.start("q", workflow_name="agent")
        collector.start_span("root", parent_id=None, name="Agent 图",
                             type="workflow")
        collector.start_span("tool_node", name="工具节点", type="tool_call")

        record_llm_call_span(metrics=dict(_META), duration_ms=10.0)
        llm_spans = [s for s in trace.spans if s.type == "llm_call"]
        assert len(llm_spans) == 1
        assert llm_spans[0].parent_id == "tool_node"


# ==========================================================
# 3. prompt 快照
# ==========================================================

class TestPromptSnapshot:
    def test_str_input(self):
        note_prompt_snapshot((("你好",),))
        assert consume_prompt_snapshot() == "你好"
        assert consume_prompt_snapshot() == ""  # 消费后复位

    def test_message_tuple(self):
        note_prompt_snapshot((("human", "问题内容"),))
        assert consume_prompt_snapshot() == "问题内容"

    def test_base_message_list(self):
        from langchain_core.messages import HumanMessage

        note_prompt_snapshot(([HumanMessage(content="消息列表最后一条")],))
        assert consume_prompt_snapshot() == "消息列表最后一条"

    def test_truncated_to_500(self):
        note_prompt_snapshot((("x" * 2000,),))
        assert len(consume_prompt_snapshot()) == 500

    def test_empty_args_safe(self):
        note_prompt_snapshot(())
        assert consume_prompt_snapshot() == ""
        note_prompt_snapshot((None,))
        assert consume_prompt_snapshot() == ""


# ==========================================================
# 4. _record_tokens 集成：proxy 结算后自动出 span
# ==========================================================

class TestRecordTokensIntegration:
    def _fake_result(self, token_usage):
        class R:
            response_metadata = {"token_usage": token_usage,
                                 "finish_reason": "stop"}

        return R()

    def test_record_tokens_creates_span(self, monkeypatch):
        collector = _fresh_tracer(monkeypatch)
        trace = collector.start("q", workflow_name="agent")
        collector.start_span("root", parent_id=None, name="Agent 图",
                             type="workflow")

        from backend.infra.llm import proxy as proxy_mod

        note_prompt_snapshot((["集成测试问题"],))
        proxy_mod._record_tokens(
            self._fake_result(
                {"prompt_tokens": 100, "completion_tokens": 50,
                 "total_tokens": 150}),
            duration_ms=800.0,
            model_name=_PRICING_TEST_MODEL,
        )
        llm_spans = [s for s in trace.spans if s.type == "llm_call"]
        assert len(llm_spans) == 1
        assert llm_spans[0].metrics["total_tokens"] == 150
        assert llm_spans[0].input == {"prompt": "集成测试问题"}

    def test_record_tokens_without_usage_no_span(self, monkeypatch):
        """无 usage 早退（未计量）不出 span——span 与 usage 行一一对应。"""
        collector = _fresh_tracer(monkeypatch)
        trace = collector.start("q", workflow_name="agent")
        collector.start_span("root", parent_id=None, name="Agent 图",
                             type="workflow")

        from backend.infra.llm import proxy as proxy_mod

        proxy_mod._record_tokens(self._fake_result(None), duration_ms=800.0)
        assert not [s for s in trace.spans if s.type == "llm_call"]


# ==========================================================
# 5. 不双计：_aggregate_usage 与 backfill 同值不叠加
# ==========================================================

class TestNoDoubleCount:
    def test_aggregate_equals_usage_row(self, monkeypatch):
        collector = _fresh_tracer(monkeypatch)
        trace = collector.start("q", workflow_name="agent")

        record_llm_call_span(metrics=dict(_META), duration_ms=100.0)
        tracer_mod.TraceCollector._aggregate_usage(trace)
        assert trace.usage["total_tokens"] == 150

        # backfill：llm_usage 一行同值 → 覆盖后仍是一次的量
        class _FakeStore:
            def by_trace(self, _trace_id):
                return [{
                    "component": "llm", "model": _PRICING_TEST_MODEL,
                    "provider": "test-provider",
                    "prompt_tokens": 100, "completion_tokens": 50,
                    "total_tokens": 150, "cached_tokens": 0,
                    "reasoning_tokens": 0,
                    "billed_cost_cny": 0.021, "billing_schema_version": 2,
                    "ts": trace.timestamp,
                }]

        import backend.observability.llm_usage_store as store_mod

        monkeypatch.setattr(store_mod, "get_llm_usage_store", lambda: _FakeStore())
        tracer_mod.TraceCollector._backfill_usage_from_store(trace)
        assert trace.usage["total_tokens"] == 150
        assert trace.usage["cost_cny"] == pytest.approx(0.021)
        # span 自带 token_source=proxy_span，backfill 不再配对占行
        span = next(s for s in trace.spans if s.type == "llm_call")
        assert span.metrics["token_source"] == "proxy_span"


# ==========================================================
# 6. 脱敏矩阵：masked/summary 不泄漏原文
# ==========================================================

class TestRedactionMatrix:
    def _trace_with_pii(self, monkeypatch) -> tracer_mod.TraceCollector:
        collector = _fresh_tracer(monkeypatch)
        collector.start("客户咨询", workflow_name="agent")
        collector.start_span("root", parent_id=None, name="Agent 图",
                             type="workflow")
        note_prompt_snapshot((["客户手机号 13812345678，邮箱 test@example.com"],))
        record_llm_call_span(
            metrics=dict(_META),
            result=_FakeResult("已登记手机号 13812345678"),
            duration_ms=50.0,
        )
        return collector

    def _llm_span(self, dto: dict) -> dict:
        spans = [s for s in dto["spans"] if s.get("type") == "llm_call"]
        assert len(spans) == 1
        return spans[0]

    def test_masked_level_no_leak(self, monkeypatch):
        from backend.app.api.routes._trace_dto import to_trace_dto

        collector = self._trace_with_pii(monkeypatch)
        trace = collector.current()
        dto = to_trace_dto(trace, detail_level="masked")
        blob = json.dumps(dto, ensure_ascii=False, default=str)
        assert "13812345678" not in blob
        assert "test@example.com" not in blob

    def test_summary_level_strips_text(self, monkeypatch):
        from backend.app.api.routes._trace_dto import to_trace_dto

        collector = self._trace_with_pii(monkeypatch)
        trace = collector.current()
        dto = to_trace_dto(trace, detail_level="summary")
        blob = json.dumps(dto, ensure_ascii=False, default=str)
        assert "13812345678" not in blob
        span = self._llm_span(dto)
        assert span["llm_call"]["prompt_text"] == ""
        assert span["llm_call"]["response_text"] == ""

    def test_full_level_keeps_raw(self, monkeypatch):
        """full 级保留原文（现行存储保真口径，redaction 不处理 full）。"""
        from backend.app.api.routes._trace_dto import to_trace_dto

        collector = self._trace_with_pii(monkeypatch)
        trace = collector.current()
        dto = to_trace_dto(trace, detail_level="full")
        span = self._llm_span(dto)
        assert "13812345678" in span["llm_call"]["prompt_text"]
