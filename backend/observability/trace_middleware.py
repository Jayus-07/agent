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
import uuid

from backend.config import STATE_KEY_GUARD_MODE
from backend.observability.metrics import state_unknown_key_total
from backend.observability.tracer import (
    bind_graph_node,
    reset_graph_node,
    trace_collector,
)
from backend.orchestration.state import validate_state_update
from backend.shared.logger import logger

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
    # 与 RoutingEngine 内部的 routing.route_decision span（同为"路由决策"）
    # 区分：本节点是图上的外层阶段，内部还嵌套 entry_gate/domain/intent/
    # capability/policy/route_decision 等细分阶段。同名会让时间线出现两行
    # 无法分辨的「路由决策」（744ms 聚合行 vs 1ms 真实决策行）。
    "router":              "路由阶段",
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


def check_state_update(node_name: str, result) -> None:
    """状态键守卫（P1-1）：节点 update 含未登记 schema 的键时告警/抛错。

    该键会被 LangGraph updates 流与 state 双静默剥离（写入点丢失，
    历史事故 ×4），必须在节点返回后、交回 LangGraph 前检查——下沉到
    runner 事件流层看不到被剥离的键（2026-09-30 实验证实）。
    log（默认）：state_unknown_key_total{node} + warning；enforce：raise。
    守卫自身异常软失败（不能因守卫弄挂节点）。正确修法 = 补登记进
    backend/orchestration/state.py，不接受 exclude 黑名单。
    """
    try:
        unknown = validate_state_update(node_name, result)
        if not unknown:
            return
        for _key in unknown:
            state_unknown_key_total.labels(node=node_name).inc()
        if STATE_KEY_GUARD_MODE == "enforce":
            raise RuntimeError(
                f"[StateKeyGuard] 节点 {node_name} 返回未登记状态键 {unknown}"
                "——将被 LangGraph 剥离；请补登记进 state.py 或修正键名"
            )
        logger.warning(
            "[StateKeyGuard] 节点 %s 返回未登记状态键 %s —— 将被 LangGraph "
            "静默剥离；请补登记进 backend/orchestration/state.py",
            node_name, unknown,
        )
    except RuntimeError:
        raise
    except Exception:  # noqa: BLE001 — 守卫软失败
        logger.debug("[StateKeyGuard] 守卫内部异常（软失败）", exc_info=True)


def guard_node_update(node_name: str, node_fn):
    """纯守卫包装（无 span）：router 等自建 span 的节点专用。

    builder 里 router 不走 wrap_sync_node（MultiTierRouter 内部自建完整
    span，双包装会产生同名重复 span），但它的 update 同样会被剥离——
    本包装只挂守卫不加 trace。
    """

    @functools.wraps(node_fn)
    def wrapper(state: dict) -> dict:
        result = node_fn(state)
        check_state_update(node_name, result)
        return result

    return wrapper


def _emit_node_status(
    *,
    node_name: str,
    execution_id: str,
    phase: str,
    started_at: float,
    finished_at: float | None = None,
    duration_ms: float | None = None,
    status: str,
) -> None:
    """发送与节点真实执行边界对齐的 SSE status 帧。"""
    try:
        from backend.orchestration.graph.sse_event_sink import emit_sse_event

        data = {
            "node": node_name,
            "ts": finished_at if finished_at is not None else started_at,
            "phase": phase,
            "execution_id": execution_id,
            "started_at": started_at,
            "status": status,
        }
        if finished_at is not None:
            data["finished_at"] = finished_at
        if duration_ms is not None:
            data["duration_ms"] = round(duration_ms, 1)
        emit_sse_event({"event": "status", "data": data})
    except Exception:
        # 进度帧是旁路观测，不能改变节点的业务结果。
        return


def _new_execution_id(node_name: str) -> str:
    return f"{node_name}:{uuid.uuid4().hex}"


class TraceMiddleware:
    """统一 Trace 中间件。

    在 LangGraph 节点执行前后自动记录 Span，消除各 Skill 中分散的 Trace 代码。
    不改变节点函数的签名和返回值。
    """

    def wrap_sync_node(self, node_name: str, node_fn):
        """包装同步节点函数（LangGraph 标准）"""

        @functools.wraps(node_fn)
        def traced_wrapper(state: dict) -> dict:
            # ── 请求上下文显式绑定（P1 重构）：节点可能跑在 LangGraph Send
            # 内部线程池，ContextVar 不跨线程继承，须从 state 重新绑定。
            # 必须在 current() 读取之前——绑定后 Send 分支的 span 才能挂上。
            from backend.orchestration.request_context import bind_from_state
            bind_from_state(state)

            trace = trace_collector.current()
            label = _NODE_LABELS.get(node_name, node_name)
            kind = _NODE_KINDS.get(node_name, "agent")
            step_id = state.get("current_step_id", "")
            question = state.get("question", "")[:80]
            span = None
            graph_token = None
            if trace is not None:
                # 图节点显式挂到 root：不参与"最近未关闭 span"推断，
                # 否则会被上一层未收口的兄弟 span 吞掉。
                span = trace_collector.start_span(
                    span_id=f"{node_name}:{step_id}" if step_id else node_name,
                    parent_id=trace.root_span_id or None,
                    name=label,
                    kind=kind,
                    input={"step_id": step_id, "question": question},
                )
                graph_token = bind_graph_node(span.span_id)

            execution_id = _new_execution_id(node_name)
            started_at = time.time()
            t0 = time.monotonic()
            _emit_node_status(
                node_name=node_name,
                execution_id=execution_id,
                phase="started",
                started_at=started_at,
                status="running",
            )
            try:
                try:
                    result = node_fn(state)
                    # 守卫在 span 收口前：enforce 抛错时 span 以 error 收口
                    check_state_update(node_name, result)
                    elapsed_ms = (time.monotonic() - t0) * 1000
                    node_status = self._node_span_status(result)
                    if span is not None:
                        trace_collector.end_span(
                            span,
                            output=self._summarize_output(result, node_name),
                            metrics={"elapsed_ms": round(elapsed_ms, 1)},
                            status=node_status,
                        )
                    finished_at = time.time()
                    _emit_node_status(
                        node_name=node_name,
                        execution_id=execution_id,
                        phase="completed",
                        started_at=started_at,
                        finished_at=finished_at,
                        duration_ms=elapsed_ms,
                        status=node_status,
                    )
                    return result
                except BaseException as e:
                    elapsed_ms = (time.monotonic() - t0) * 1000
                    cancelled = isinstance(e, (GeneratorExit, KeyboardInterrupt)) or (
                        type(e).__name__ == "CancelledError"
                    )
                    event_status = "cancelled" if cancelled else "error"
                    if span is not None:
                        trace_collector.end_span(
                            span,
                            status=event_status,
                            metrics={
                                "elapsed_ms": round(elapsed_ms, 1),
                                "error": str(e)[:200],
                            },
                        )
                    finished_at = time.time()
                    _emit_node_status(
                        node_name=node_name,
                        execution_id=execution_id,
                        phase="cancelled" if cancelled else "failed",
                        started_at=started_at,
                        finished_at=finished_at,
                        duration_ms=elapsed_ms,
                        status=event_status,
                    )
                    raise
            finally:
                if graph_token is not None:
                    reset_graph_node(graph_token)

        @functools.wraps(node_fn)
        def wrapper(state: dict) -> dict:
            from backend.prompts.service import prompt_service

            with prompt_service.bind_prompt_versions(state.get("prompt_versions")):
                return traced_wrapper(state)

        return wrapper

    def wrap_async_node(self, node_name: str, node_fn):
        """包装异步节点函数"""

        @functools.wraps(node_fn)
        async def traced_wrapper(state: dict) -> dict:
            trace = trace_collector.current()
            label = _NODE_LABELS.get(node_name, node_name)
            kind = _NODE_KINDS.get(node_name, "agent")
            step_id = state.get("current_step_id", "")
            question = state.get("question", "")[:80]
            span = None
            graph_token = None
            if trace is not None:
                # 图节点显式挂到 root：不参与"最近未关闭 span"推断，
                # 否则会被上一层未收口的兄弟 span 吞掉。
                span = trace_collector.start_span(
                    span_id=f"{node_name}:{step_id}" if step_id else node_name,
                    parent_id=trace.root_span_id or None,
                    name=label,
                    kind=kind,
                    input={"step_id": step_id, "question": question},
                )
                graph_token = bind_graph_node(span.span_id)

            execution_id = _new_execution_id(node_name)
            started_at = time.time()
            t0 = time.monotonic()
            _emit_node_status(
                node_name=node_name,
                execution_id=execution_id,
                phase="started",
                started_at=started_at,
                status="running",
            )
            try:
                try:
                    result = await node_fn(state)
                    check_state_update(node_name, result)
                    elapsed_ms = (time.monotonic() - t0) * 1000
                    node_status = self._node_span_status(result)
                    if span is not None:
                        trace_collector.end_span(
                            span,
                            output=self._summarize_output(result, node_name),
                            metrics={"elapsed_ms": round(elapsed_ms, 1)},
                            status=node_status,
                        )
                    finished_at = time.time()
                    _emit_node_status(
                        node_name=node_name,
                        execution_id=execution_id,
                        phase="completed",
                        started_at=started_at,
                        finished_at=finished_at,
                        duration_ms=elapsed_ms,
                        status=node_status,
                    )
                    return result
                except BaseException as e:
                    elapsed_ms = (time.monotonic() - t0) * 1000
                    cancelled = type(e).__name__ == "CancelledError"
                    event_status = "cancelled" if cancelled else "error"
                    if span is not None:
                        trace_collector.end_span(
                            span,
                            status=event_status,
                            metrics={
                                "elapsed_ms": round(elapsed_ms, 1),
                                "error": str(e)[:200],
                            },
                        )
                    finished_at = time.time()
                    _emit_node_status(
                        node_name=node_name,
                        execution_id=execution_id,
                        phase="cancelled" if cancelled else "failed",
                        started_at=started_at,
                        finished_at=finished_at,
                        duration_ms=elapsed_ms,
                        status=event_status,
                    )
                    raise
            finally:
                if graph_token is not None:
                    reset_graph_node(graph_token)

        @functools.wraps(node_fn)
        async def wrapper(state: dict) -> dict:
            from backend.prompts.service import prompt_service

            with prompt_service.bind_prompt_versions(state.get("prompt_versions")):
                return await traced_wrapper(state)

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

    @staticmethod
    def _node_span_status(result: dict) -> str:
        """按业务结果决定节点 span 状态。

        Why: 节点函数正常返回不代表业务成功——step_results 里可能是 failed
        （如 RAG 工具连接错误）。旧实现一律写 success，于是出现"工具 error、
        所在节点 success"的矛盾，掩盖真实失败原因。这里按 step_results 的
        失败面降级为 error/partial，保留 success 语义不被滥用。
        """
        if not isinstance(result, dict):
            return "success"
        step_results = result.get("step_results") or {}
        if not isinstance(step_results, dict):
            return "success"
        statuses = [
            str(sr.get("status") or "").lower()
            for sr in step_results.values()
            if isinstance(sr, dict)
        ]
        failed = {"failed", "error", "timeout", "permission_denied",
                  "validation_error", "syntax_error", "no_table", "unavailable"}
        if any(status in failed for status in statuses):
            succeeded = {"success", "no_data", "done", "partial"}
            return "partial" if any(s in succeeded for s in statuses) else "error"
        return "success"


# 全局单例
trace_middleware = TraceMiddleware()
