"""
backend/orchestration/graph/engine/trace_builder.py — Trace 重建、Guard 审计与图拓扑快照
"""
from __future__ import annotations

import logging
from typing import Any

from backend.shared.logger import logger


def add_guard_span(guard_result) -> None:
    """把 Input Guard 判定记录为 span（挂在 root 下，埋点失败不影响主流程）。"""
    from backend.observability.tracer import trace_collector
    try:
        span = trace_collector.start_span(
            "input_guard", name="Input Guard", type="workflow",
            input={"query_len": len(guard_result.normalized_query)},
        )
        trace_collector.end_span(
            span,
            output={
                "action": guard_result.action.value,
                "category": guard_result.category.value,
                "risk_level": guard_result.risk_level.value,
                "confidence": guard_result.confidence,
                "reason": guard_result.reason,
            },
            metrics={
                "layer": guard_result.layer,
                "llm_consulted": guard_result.llm_consulted,
                "needs_permission": guard_result.needs_permission,
                "domain": guard_result.domain or "",
                "policy_version": guard_result.policy_version,
            },
            status="success",
        )
    except Exception:
        logger.debug("[GraphRunner] Guard span 记录失败", exc_info=True)


def finish_guard_trace(session_id: str, guard_result) -> None:
    """为被 Guard 拦截的请求产出一条最小 trace（留 rejected 证据）。

    安全约束：trace 的 question 不落拦截请求原文（可能是恶意/敏感内容），
    用类别占位符替代；原文取证信息在 Guard 审计日志（sha256 摘要）中。
    """
    from backend.observability.tracer import trace_collector
    try:
        trace = trace_collector.start(
            f"[guard-rejected:{guard_result.category.value}]",
            session_id, workflow_name="agent",
        )
        span = trace_collector.start_span(
            "input_guard", parent_id=None,
            name="Input Guard 拦截", type="workflow",
        )
        trace_collector.end_span(
            span,
            output={
                "action": guard_result.action.value,
                "category": guard_result.category.value,
                "risk_level": guard_result.risk_level.value,
                "confidence": guard_result.confidence,
            },
            metrics={"policy_version": guard_result.policy_version},
            status="success",
        )
        trace_collector.finish(
            trace, guard_result.message or "", 0, "", "",
        )
    except Exception:
        logger.debug("[GraphRunner] Guard 拦截 trace 记录失败", exc_info=True)


def finish_customer_service_guard_trace(session_id: str, guard_result) -> None:
    """为客服业务门禁拦截产出最小 trace，不记录敏感原文。"""
    from backend.observability.tracer import trace_collector

    try:
        category = getattr(guard_result.category, "value", "unknown")
        action = getattr(guard_result.action, "value", "unknown")
        trace = trace_collector.start(
            f"[cs-guard-rejected:{category}]",
            session_id,
            workflow_name="agent",
        )
        span = trace_collector.start_span(
            "cs_input_guard",
            parent_id=None,
            name="CS Input Guard 拦截",
            type="workflow",
        )
        trace_collector.end_span(
            span,
            output={
                "action": action,
                "category": category,
                "reason": guard_result.reason,
            },
            status="success",
        )
        trace_collector.finish(
            trace,
            guard_result.message or "",
            0,
            "",
            "",
        )
    except Exception:
        logger.debug(
            "[GraphRunner] CS Input Guard 拦截 trace 记录失败",
            exc_info=True,
        )


def _persist_cs_turn_if_needed(
    cs_context: dict,
    session_id: str,
    question: str,
    answer: str,
    trace_id: str,
    tenant_id: str = "",
    entry_order_no: str = "",
) -> None:
    """Fire-and-forget CS turn persistence — no-op when not a CS request.

    STOP CS-A P0-2：租户显式传递给 record_cs_turn（keyword-only 必传），
    优先取身份链的 tenant_id，cs_context 快照兜底；两者皆空时由
    conversation_store fail-closed 跳过落库。
    """
    if not cs_context or not cs_context.get("conversation_id"):
        return
    try:
        from backend.customer_service.conversation_store import record_cs_turn
        record_cs_turn(
            conversation_id=cs_context["conversation_id"],
            user_id=cs_context.get("authenticated_user_id", "anonymous"),
            question=question,
            answer=answer,
            trace_id=trace_id,
            cs_route=cs_context.get("cs_route"),
            tenant_id=str(tenant_id or cs_context.get("tenant_id") or ""),
            question_content_type="card" if entry_order_no else "text",
            question_attachments=(
                [{"type": "order_ref", "order_no": entry_order_no}]
                if entry_order_no else None
            ),
        )
    except Exception:
        logger.debug("[GraphRunner] CS turn persistence failed", exc_info=True)


def _end_root(trace, output: dict = None, metrics: dict = None,
              status: str = "success") -> None:
    """查找并结束 root span。"""
    from backend.observability.tracer import trace_collector
    for sp in trace.spans:
        if sp.parent_id is None:
            trace_collector.end_span(sp, output=output, metrics=metrics, status=status)
            return


def _fallback_summary_from_results(step_results: dict) -> str:
    """无最终答案时复用 Reporter 的纯规则渲染，避免 dict/异常原文外泄。"""
    from backend.agents.reporter.reporter import render_step_results_deterministically

    if not step_results:
        return "## 无结果\n\n未能获取任何有效数据。"
    return render_step_results_deterministically(step_results)


def _count_rounds_from_results(step_results: dict) -> int:
    """从 step_results 估算 supervisor 循环次数（事件流路径用）。"""
    if not step_results:
        return 0
    completed = sum(1 for sr in step_results.values()
                    if sr.get("status") in ("success", "failed", "skipped"))
    return max(1, completed)


def trace_from_state(trace, state: dict) -> None:
    """从 LangGraph final_state 重建执行过程 Span 树。

    invoke() 是黑盒，不能逐节点 trace；此方法从最终状态中提取执行痕迹：
      - Planner → span(type=agent, kind=graph_node)
      - Critique → span(type=agent, kind=graph_node)
      - Supervisor Round N → span(type=workflow, kind=graph_loop)
      - Skill 执行 → span(type=agent, kind=internal)
      - Reporter → span(type=agent, kind=graph_node)
      - LangGraph 拓扑 → trace.graph
    """
    from backend.observability.tracer import Span

    plan = state.get("plan", {})
    nodes = plan.get("nodes", {})
    step_results = dict(state.get("step_results", {}))
    loop_count = state.get("_supervisor_loop_count", 0)
    degraded_steps = state.get("_degraded_steps", set())
    plan_changed = state.get("_plan_changed", False)
    route_mode = state.get("route_mode") or "plan"

    existing_ids = {s.span_id for s in trace.spans}

    def _has_node_span(node: str) -> bool:
        return any(sid == node or sid.startswith(f"{node}:") for sid in existing_ids)

    def _has_middleware_span(step_id: str) -> bool:
        return any(sid.endswith(f":{step_id}") for sid in existing_ids)

    # ── Planner（仅在 plan 模式执行过且中间件 span 缺失时补）──
    if route_mode == "plan" and nodes and not _has_node_span("planner"):
        planner_span = Span(
            span_id="planner", parent_id="root",
            name="Planner 拆解", type="agent",
            status="success",
            metrics={"subtasks": len(nodes), "synthesized": True},
        )
        trace.spans.append(planner_span)

    # ── Critique ──
    if route_mode == "plan" and state.get("_plan_critiqued") and not _has_node_span("critique"):
        crit_span = Span(
            span_id="critique", parent_id="root",
            name="计划审查", type="agent",
            status="success",
            metrics={"plan_changed": plan_changed, "synthesized": True},
        )
        trace.spans.append(crit_span)

    # ── Supervisor Rounds（direct/workflow 模式无调度轮次）──
    executed_rounds = sum(
        1 for sid, sr in step_results.items()
        if sid in nodes and sr.get("status", "pending") not in ("pending", "running")
    ) if route_mode == "plan" else 0
    for r in range(1, min(loop_count, executed_rounds) + 1):
        if _has_node_span("supervisor"):
            break
        round_span = Span(
            span_id=f"supervisor-round-{r}", parent_id="root",
            name=f"调度轮次 {r}", type="workflow",
            status="success",
            metrics={"round": r, "synthesized": True},
        )
        trace.spans.append(round_span)

    # ── Skills（从 step_results 重建）──
    for step_id, sr in step_results.items():
        cap = sr.get("capability", "")
        status = sr.get("status", "pending")
        desc = sr.get("description", step_id)
        if status in ("pending", "running"):
            continue
        if _has_middleware_span(step_id):
            continue

        err = sr.get("error", "") or (
            f"步骤执行失败（状态={status}）" if status not in ("success", "skipped") else "")
        skill_span = Span(
            span_id=f"skill-{step_id}", parent_id="root",
            name=desc, type="agent",
            status=status if status in ("success", "error", "skipped") else "error",
            metrics={
                "capability": cap,
                "error": err,
                "retry_count": sr.get("retries", 0),
                "synthesized": True,
            },
        )
        trace.spans.append(skill_span)

    # ── Reporter（执行过但中间件 span 缺失才补）──
    final_answer = state.get("final_answer", "")
    if final_answer and not _has_node_span("reporter"):
        reporter_span = Span(
            span_id="reporter", parent_id="root",
            name="Reporter 汇总", type="agent",
            status="success" if final_answer else "error",
            metrics={"answer_len": len(final_answer), "synthesized": True},
            output={"answer_preview": final_answer[:200]} if final_answer else None,
        )
        trace.spans.append(reporter_span)

    # ── Graph 拓扑 ──
    trace.graph = _build_graph_snapshot(state, loop_count, degraded_steps)


def _build_graph_snapshot(state: dict, loop_count: int,
                          degraded_steps: set) -> dict:
    """从执行状态构建 LangGraph 拓扑快照（按实际路由模式区分）。"""
    from backend.orchestration.supervisor.scheduler import MAX_SUPERVISOR_LOOPS

    route_mode = state.get("route_mode") or "plan"
    if route_mode in ("direct", "workflow"):
        executor_id = "skill_executor" if route_mode == "direct" else "workflow_executor"
        executor_label = "直接执行" if route_mode == "direct" else "工作流执行"
        graph_nodes = [
            {"id": "router", "label": "路由决策"},
            {"id": executor_id, "label": executor_label},
            {"id": "reporter", "label": "Reporter 汇总"},
        ]
        graph_edges = [
            {"source": "router", "target": executor_id, "label": route_mode},
            {"source": executor_id, "target": "reporter", "label": "完成"},
        ]
        return {
            "nodes": graph_nodes,
            "edges": graph_edges,
            "max_loops": MAX_SUPERVISOR_LOOPS,
            "loop_count": loop_count,
            "degradation_triggered": len(degraded_steps) > 0 if degraded_steps else False,
        }

    # ── 短路直出模式（追问/域引导）：Router → Reporter，未执行任何 Skill ──
    if route_mode in ("clarify", "handoff"):
        return {
            "nodes": [
                {"id": "router", "label": "路由决策"},
                {"id": "reporter", "label": "Reporter 汇总"},
            ],
            "edges": [
                {"source": "router", "target": "reporter", "label": route_mode},
            ],
            "max_loops": 0,
            "loop_count": 0,
            "degradation_triggered": False,
        }

    # ── 域图模式：Router → 域图节点 → END（通过 registry 动态查找）──
    from backend.orchestration.domain_registry import domain_graph_registry
    domain = domain_graph_registry.get(route_mode)
    if domain:
        graph_nodes = [
            {"id": "router", "label": "路由决策"},
            {"id": domain.node_name, "label": domain.label},
        ]
        graph_edges = [
            {"source": "router", "target": domain.node_name, "label": route_mode},
        ]
        return {
            "nodes": graph_nodes,
            "edges": graph_edges,
            "max_loops": 0,
            "loop_count": 0,
            "degradation_triggered": False,
        }

    # ── plan 模式：Router → Planner → Critique → Supervisor → Skills → Reporter ──
    plan = state.get("plan", {})
    plan_nodes = plan.get("nodes", {})

    graph_nodes = [
        {"id": "router", "label": "路由决策"},
        {"id": "planner", "label": "Planner 拆解"},
        {"id": "critique", "label": "计划审查"},
        {"id": "supervisor", "label": "Supervisor 调度"},
    ]
    for sid, node_info in plan_nodes.items():
        graph_nodes.append({
            "id": f"skill-{sid}",
            "label": node_info.get("description", sid),
        })
    graph_nodes.append({"id": "reporter", "label": "Reporter 汇总"})

    graph_edges = [
        {"source": "router", "target": "planner", "label": "plan"},
        {"source": "planner", "target": "critique"},
        {"source": "critique", "target": "supervisor"},
    ]
    for sid in plan_nodes:
        graph_edges.append({"source": "supervisor", "target": f"skill-{sid}", "label": "dispatch"})
        graph_edges.append({"source": f"skill-{sid}", "target": "supervisor", "label": "完成"})
    graph_edges.append({"source": "supervisor", "target": "reporter", "label": "all_done"})

    return {
        "nodes": graph_nodes,
        "edges": graph_edges,
        "max_loops": MAX_SUPERVISOR_LOOPS,
        "loop_count": loop_count,
        "degradation_triggered": len(degraded_steps) > 0 if degraded_steps else False,
    }
