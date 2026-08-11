"""router_node.py — Router LangGraph 节点（2026-08-11）

3 层 fallback Router 接入 LangGraph：
  - Router 节点在 graph 入口
  - RouteDecision 存到 state.route_decision
  - conditional_edges 按 execution_mode 分流

V1 实现:
  - plan mode → 走现有 planner → critique → supervisor 路径
  - workflow mode → stub（V1 未完整实现，后续接 workflow_name）
  - direct mode → V1 降级为 plan（后续可加 single-skill node）
"""
from __future__ import annotations

import asyncio

from backend.orchestration.router import get_router
from backend.orchestration.router.types import ExecutionMode
from backend.shared.logger import logger


def router_node(state: dict) -> dict:
    """Router 节点：执行 3 层 fallback 路由，存 decision 到 state。

    Returns:
        包含 route_decision 的 state 更新
    """
    query = state.get("question") or state.get("query") or ""
    if not query:
        # 无 query → 默认走 plan
        return {
            **state,
            "route_decision": None,
            "route_mode": "plan",
        }

    try:
        router = get_router()
        # 同步调用（router 主流程是同步的）
        decision = router.route(query)
        logger.info(
            f"[RouterNode] mode={decision.execution_mode.value} "
            f"conf={decision.confidence:.2f} "
            f"cands={[(c.name, c.score) for c in decision.candidates]}"
        )
    except Exception as e:
        logger.warning(f"[RouterNode] 路由失败，回退到 plan: {e}")
        return {
            **state,
            "route_decision": None,
            "route_mode": "plan",
        }

    # V1: workflow mode 也走 plan 路径（后续接 workflow_name）
    mode = decision.execution_mode
    if mode == ExecutionMode.WORKFLOW:
        # 暂未实现 workflow 节点 → 走 plan 路径但保留 workflow_name hint
        logger.info(
            f"[RouterNode] workflow={decision.workflow_name} "
            f"暂未实现，降级到 plan 路径"
        )
        return {
            **state,
            "route_decision": decision.model_dump(),
            "route_mode": "plan",
            "route_workflow_hint": decision.workflow_name,
        }

    return {
        **state,
        "route_decision": decision.model_dump(),
        "route_mode": mode.value,
    }


def route_selector(state: dict) -> str:
    """Router 节点后的条件路由：选择下一步节点。"""
    mode = state.get("route_mode", "plan")
    if mode == "plan":
        return "planner"
    # V1: direct / workflow 都先到 planner
    return "planner"
