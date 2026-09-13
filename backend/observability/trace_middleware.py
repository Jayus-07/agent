"""
observability/trace_middleware.py — 统一 Trace 中间件

在 LangGraph 节点执行前后自动记录 Span，各 Skill 不再手动管理 Trace。
使用现有 trace_collector API（start_span / end_span），不改造 TraceCollector 核心。

使用方式:
    # 在 graph builder 中包装 Skill 节点
    middleware = TraceMiddleware()
    wrapped_fn = middleware.wrap_node("sql_skill", sql_skill_node)
    wf.add_node("sql_skill", wrapped_fn)
"""

import functools
import time

from backend.observability.tracer import trace_collector

# 节点名 → 用户可读标签
_NODE_LABELS: dict[str, str] = {
    "planner":              "任务规划",
    "critique":             "计划审查",
    "supervisor":           "调度决策",
    "sql_skill":            "数据库查询",
    "rag_skill":            "知识库检索",
    "report_skill":         "报告生成",
    "reporter":             "结果汇总",
    "business_analysis_skill": "业务分析",
    "router":              "路由决策",
    "tool_selector":       "工具选择",
    "skill_executor":      "直接执行",
    "workflow_executor":   "工作流执行",
    # CS nodes
    "cs_knowledge":         "客服知识问答",
    "cs_business_query":    "业务查询",
    "cs_business_action":   "业务操作",
    "cs_complaint":         "投诉处理",
    "cs_handoff":           "转接人工",
    "cs_handoff_intercept": "转接拦截",
    "cs_pending":           "待处理",
    # CS Graph 独立架构节点
    "cs_state_loader":      "状态加载",
    "cs_supervisor":        "客服调度",
    "cs_knowledge_expert":  "知识专家",
    "cs_reporter":          "客服汇总",
    "cs_graph_node":        "客服图适配",
}

_NODE_KINDS: dict[str, str] = {
    "cs_knowledge":         "cs_knowledge",
    "cs_business_query":    "cs_business_query",
    "cs_business_action":   "cs_business_action",
    "cs_complaint":         "cs_complaint",
    "cs_handoff":           "cs_handoff",
    "cs_handoff_intercept": "cs_handoff",
    "cs_pending":           "cs_confirmation",
    # CS Graph 独立架构节点
    "cs_state_loader":      "cs_state_loader",
    "cs_supervisor":        "cs_supervisor",
    "cs_knowledge_expert":  "cs_expert",
    "cs_reporter":          "cs_reporter",
    "cs_graph_node":        "cs_graph",
}


class TraceMiddleware:
    """统一 Trace 中间件。

    在 LangGraph 节点执行前后自动记录 Span，消除各 Skill 中分散的 Trace 代码。
    不改变节点函数的签名和返回值。
    """

    def wrap_sync_node(self, node_name: str, node_fn):
        """包装同步节点函数（LangGraph 标准）"""

        @functools.wraps(node_fn)
        def wrapper(state: dict) -> dict:
            # ── 请求上下文显式绑定（P1 重构）：节点可能跑在 LangGraph Send
            # 内部线程池，ContextVar 不跨线程继承，须从 state 重新绑定。
            # 必须在 current() 读取之前——绑定后 Send 分支的 span 才能挂上。
            from backend.orchestration.request_context import bind_from_state
            bind_from_state(state)

            trace = trace_collector.current()
            if trace is None:
                return node_fn(state)

            label = _NODE_LABELS.get(node_name, node_name)
            kind = _NODE_KINDS.get(node_name, "agent")
            step_id = state.get("current_step_id", "")
            question = state.get("question", "")[:80]

            span = trace_collector.start_span(
                span_id=f"{node_name}:{step_id}" if step_id else node_name,
                name=label,
                kind=kind,
                input={
                    "step_id": step_id,
                    "question": question,
                },
            )

            t0 = time.monotonic()
            try:
                result = node_fn(state)
                elapsed_ms = (time.monotonic() - t0) * 1000
                trace_collector.end_span(
                    span,
                    output=self._summarize_output(result, node_name),
                    metrics={"elapsed_ms": round(elapsed_ms, 1)},
                    status="success",
                )
                return result
            except Exception as e:
                elapsed_ms = (time.monotonic() - t0) * 1000
                trace_collector.end_span(
                    span,
                    status="error",
                    metrics={
                        "elapsed_ms": round(elapsed_ms, 1),
                        "error": str(e)[:200],
                    },
                )
                raise

        return wrapper

    def wrap_async_node(self, node_name: str, node_fn):
        """包装异步节点函数"""

        @functools.wraps(node_fn)
        async def wrapper(state: dict) -> dict:
            trace = trace_collector.current()
            if trace is None:
                return await node_fn(state)

            label = _NODE_LABELS.get(node_name, node_name)
            kind = _NODE_KINDS.get(node_name, "agent")
            step_id = state.get("current_step_id", "")
            question = state.get("question", "")[:80]

            span = trace_collector.start_span(
                span_id=f"{node_name}:{step_id}" if step_id else node_name,
                name=label,
                kind=kind,
                input={
                    "step_id": step_id,
                    "question": question,
                },
            )

            t0 = time.monotonic()
            try:
                result = await node_fn(state)
                elapsed_ms = (time.monotonic() - t0) * 1000
                trace_collector.end_span(
                    span,
                    output=self._summarize_output(result, node_name),
                    metrics={"elapsed_ms": round(elapsed_ms, 1)},
                    status="success",
                )
                return result
            except Exception as e:
                elapsed_ms = (time.monotonic() - t0) * 1000
                trace_collector.end_span(
                    span,
                    status="error",
                    metrics={
                        "elapsed_ms": round(elapsed_ms, 1),
                        "error": str(e)[:200],
                    },
                )
                raise

        return wrapper

    @staticmethod
    def _summarize_output(result: dict, node_name: str) -> dict:
        """提取关键输出信息，避免将整行数据写入 Trace"""
        summary: dict = {}

        step_results = result.get("step_results", {})
        for sid, sr in step_results.items():
            if not isinstance(sr, dict):
                continue
            summary[f"step_{sid}_status"] = sr.get("status", "?")
            row_count = sr.get("row_count")
            if row_count is not None:
                summary[f"step_{sid}_rows"] = row_count
            error = sr.get("error")
            if error:
                summary[f"step_{sid}_error"] = str(error)[:100]

        return summary


# 全局单例
trace_middleware = TraceMiddleware()
