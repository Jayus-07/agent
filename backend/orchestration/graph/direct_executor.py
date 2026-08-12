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

    2026-08-11 V2: 真正调 WorkflowScheduler.run_now() 跑 workflow
    """
    import asyncio
    from backend.orchestration.workflow.scheduler import get_workflow_scheduler

    decision = state.get("route_decision") or {}
    wf_name = decision.get("workflow_name") if isinstance(decision, dict) else None

    if not wf_name:
        logger.warning("[WorkflowExecutor] 无 workflow_name，降级到 plan")
        return {
            **state,
            "route_mode": "plan",
            "executor_error": "no_workflow_name",
        }

    logger.info(f"[WorkflowExecutor] 运行 workflow: {wf_name}")

    try:
        scheduler = get_workflow_scheduler()
        # run_now 是 async
        ctx = asyncio.run(scheduler.run_now(wf_name, inputs={
            "question": state.get("question", ""),
            "session_id": state.get("session_id", ""),
        }))

        # 构造 final_answer：汇总所有 step outputs
        answer_parts = [f"## 工作流 {wf_name} 执行结果\n"]
        if ctx.status == "failed":
            answer_parts.append(f"❌ 失败: {ctx.error or '未知错误'}\n")
        else:
            for step_name, output in ctx.outputs.items():
                if output:
                    answer_parts.append(f"### {step_name}\n{str(output)[:500]}\n")
        answer_parts.append(f"\n---\n*状态: {ctx.status} | run_id: {ctx.run_id}*")

        final_answer = "\n".join(answer_parts)
        # 设 step_results 防 reporter 覆盖 final_answer（workflow executor 不走 planner 管线）
        step_results = {
            f"workflow_{wf_name}": {
                "step_id": f"workflow_{wf_name}",
                "capability": "workflow",
                "description": f"工作流 {wf_name} 执行",
                "status": ctx.status,
                "output": final_answer,
                "row_count": len(ctx.outputs),
            }
        }
        return {
            **state,
            "final_answer": final_answer,
            "step_results": step_results,
            "workflow_result": {
                "status": ctx.status,
                "run_id": ctx.run_id,
                "outputs": {k: str(v)[:200] for k, v in ctx.outputs.items()},
                "error": ctx.error,
            },
            "executor_mode": "workflow",
            "executor_workflow": wf_name,
        }
    except Exception as e:
        logger.error(f"[WorkflowExecutor] {wf_name} 失败: {e}")
        error_msg = f"## 工作流 {wf_name} 失败\n\n{str(e)}"
        return {
            **state,
            "final_answer": error_msg,
            "step_results": {
                f"workflow_{wf_name}": {
                    "step_id": f"workflow_{wf_name}",
                    "capability": "workflow",
                    "description": f"工作流 {wf_name} 执行",
                    "status": "failed",
                    "output": error_msg,
                    "error": str(e),
                }
            },
            "executor_error": f"workflow_failed:{wf_name}:{e}",
            "executor_mode": "workflow",
            "executor_workflow": wf_name,
        }
