"""direct_executor.py — Direct / Workflow 模式执行器（V2，2026-08-11）

Router 决定 execution_mode=direct 或 workflow 时：
- direct: 跳过 Planner，直接调 candidates[0] 的 skill
- workflow: 调已注册 workflow（daily_report / inventory_alert）

优势:
  - 简单问题（"查库存数量"）不进 Planner，不生成 DAG
  - 速度：plan 路径 ~10s → direct 路径 ~3s
"""
from __future__ import annotations

import asyncio

from backend.orchestration.tool_registry import tool_registry
from backend.orchestration.router.types import ExecutionMode
from backend.shared.logger import logger


def _extract_capability_name(capability: str) -> str:
    """sql.query → sql_skill（tool_registry 的 node 名）。"""
    # SQL: sql.query → sql_skill
    parts = capability.split(".")
    if len(parts) == 2:
        return f"{parts[0]}_skill"
    return capability


def skill_executor_node(state: dict) -> dict:
    """direct mode: 跳过 Planner，直接调 candidates[0] 的 skill。

    路径:
      candidates[0] → tool_registry.get_skill_node(name) → 调 → 写 step_results
    """
    decision = state.get("route_decision") or {}
    candidates = decision.get("candidates", []) if isinstance(decision, dict) else []

    if not candidates:
        # 无 candidates → 降级到 plan（让 Reporter 给出降级提示）
        logger.warning("[SkillExecutor] 无 candidates，降级到 plan")
        return {
            **state,
            "route_mode": "plan",  # 触发 route_selector 失败回退
            "executor_error": "no_candidates",
        }

    top = candidates[0]
    cap_name = top.get("name", "")
    score = top.get("score", 0.0)
    node_name = _extract_capability_name(cap_name)

    logger.info(
        f"[SkillExecutor] direct: {cap_name} (score={score:.2f}) → {node_name}"
    )

    # 从 tool_registry 找 skill
    skill_nodes = tool_registry.get_skill_nodes()
    if node_name not in skill_nodes:
        logger.warning(
            f"[SkillExecutor] skill 节点 {node_name} 不存在，降级到 plan"
        )
        return {
            **state,
            "route_mode": "plan",
            "executor_error": f"skill_not_found:{node_name}",
        }

    # 构造 step（供 reporter 读取）
    step_id = "direct_1"
    step = {
        "step_id": step_id,
        "capability": cap_name,
        "description": f"直接执行 {cap_name}",
        "status": "running",
        "params": state.get("kb_id", "default"),  # 透传
    }
    state["step_results"] = {step_id: step}

    # 调 skill（skill 是 async，用 asyncio.run）
    try:
        skill_func = skill_nodes[node_name]
        result = asyncio.run(skill_func({
            "step_id": step_id,
            "question": state.get("question", ""),
            "capability": cap_name,
            "params": state.get("kb_id", "default"),
            "step_results": state.get("step_results", {}),
        }))
        step["status"] = "success"
        step["output"] = result
        logger.info(f"[SkillExecutor] {cap_name} 完成: {len(str(result))} chars")
    except Exception as e:
        step["status"] = "failed"
        step["error"] = str(e)
        logger.error(f"[SkillExecutor] {cap_name} 失败: {e}")

    return {
        **state,
        "step_results": {step_id: step},
        "final_answer": step.get("output", ""),
        "executor_mode": "direct",
    }


def workflow_executor_node(state: dict) -> dict:
    """workflow mode: 调已注册的 workflow（daily_report / inventory_alert）。

    V1: 占位（暂未完整实现），先 mark as running。
    """
    decision = state.get("route_decision") or {}
    wf_name = decision.get("workflow_name") if isinstance(decision, dict) else None

    logger.info(f"[WorkflowExecutor] workflow={wf_name}（V1 占位，后续实现）")

    # V1: 给出占位 answer，让 reporter 知道是 workflow 模式
    return {
        **state,
        "final_answer": f"[Workflow 占位] {wf_name} 暂未完整实现，V2 将调 workflow_runner",
        "executor_mode": "workflow",
        "executor_workflow": wf_name,
    }
