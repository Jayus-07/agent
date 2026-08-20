"""trace_middleware.py 单元测试 — LangGraph 节点统一埋点中间件。

此前零测试覆盖。验证点：
- 无 active trace 时直通（不建 span、不吞返回值）
- 同步/异步节点成功路径：span 名称映射、elapsed_ms、输出摘要
- 异常路径：error span 记录后原样抛出
- _summarize_output 只提取状态/行数/错误摘要，不落整行数据
"""
import asyncio

import pytest

from backend.observability.trace_middleware import TraceMiddleware, _NODE_LABELS
from backend.observability.tracer import trace_collector


@pytest.fixture(autouse=True)
def _clean_trace():
    """每用例前后复位全局 collector，避免跨用例污染。"""
    trace_collector.clear_for_test()
    yield
    trace_collector.clear_for_test()


@pytest.fixture
def mw():
    return TraceMiddleware()


class TestNoActiveTrace:

    def test_sync_passthrough_without_trace(self, mw):
        calls = []

        def node(state):
            calls.append(state)
            return {"ok": True}

        wrapped = mw.wrap_sync_node("sql_skill", node)
        assert wrapped({"x": 1}) == {"ok": True}
        assert calls == [{"x": 1}]

    def test_async_passthrough_without_trace(self, mw):
        async def node(state):
            return {"ok": True}

        wrapped = mw.wrap_async_node("rag_skill", node)
        assert asyncio.run(wrapped({"x": 1})) == {"ok": True}


class TestSyncNode:

    def test_success_creates_span_with_label(self, mw):
        trace = trace_collector.start(question="测试问题")

        def node(state):
            return {"step_results": {"s1": {"status": "success", "row_count": 3}}}

        wrapped = mw.wrap_sync_node("sql_skill", node)
        result = wrapped({"current_step_id": "s1", "question": "测试问题"})

        assert result["step_results"]["s1"]["status"] == "success"
        assert len(trace.spans) == 1
        span = trace.spans[0]
        assert span.name == _NODE_LABELS["sql_skill"] == "数据库查询"
        assert span.span_id == "sql_skill:s1"
        assert span.kind == "agent"
        assert span.status == "success"
        assert span.metrics["elapsed_ms"] >= 0
        # 输出摘要提取了状态与行数
        assert span.output["step_s1_status"] == "success"
        assert span.output["step_s1_rows"] == 3

    def test_unknown_node_name_uses_raw_name(self, mw):
        trace = trace_collector.start(question="q")
        wrapped = mw.wrap_sync_node("custom_node", lambda s: {})
        wrapped({})
        assert trace.spans[0].name == "custom_node"

    def test_exception_records_error_span_and_reraises(self, mw):
        trace = trace_collector.start(question="q")

        def boom(state):
            raise ValueError("节点炸了")

        wrapped = mw.wrap_sync_node("rag_skill", boom)
        with pytest.raises(ValueError, match="节点炸了"):
            wrapped({})

        span = trace.spans[0]
        assert span.status == "error"
        assert "节点炸了" in span.metrics["error"]


class TestAsyncNode:

    def test_success_and_error_paths(self, mw):
        trace = trace_collector.start(question="q")

        async def ok_node(state):
            await asyncio.sleep(0)
            return {}

        async def bad_node(state):
            await asyncio.sleep(0)
            raise RuntimeError("async 失败")

        assert asyncio.run(mw.wrap_async_node("report_skill", ok_node)({})) == {}
        with pytest.raises(RuntimeError, match="async 失败"):
            asyncio.run(mw.wrap_async_node("planner", bad_node)({}))

        assert len(trace.spans) == 2
        assert trace.spans[0].status == "success"
        assert trace.spans[0].name == "报告生成"
        assert trace.spans[1].status == "error"
        assert "async 失败" in trace.spans[1].metrics["error"]


class TestSummarizeOutput:

    def test_only_summary_fields_extracted(self):
        result = {
            "step_results": {
                "s1": {"status": "success", "row_count": 10},
                "s2": {"status": "error", "error": "SQL 语法错误" * 50},
                "s3": "非 dict 应被跳过",
            }
        }
        summary = TraceMiddleware._summarize_output(result, "sql_skill")
        assert summary["step_s1_status"] == "success"
        assert summary["step_s1_rows"] == 10
        assert summary["step_s2_status"] == "error"
        assert len(summary["step_s2_error"]) == 100, "错误信息必须截断到 100 字符"
        assert not any("s3" in k for k in summary)

    def test_empty_result_returns_empty_summary(self):
        assert TraceMiddleware._summarize_output({}, "x") == {}
