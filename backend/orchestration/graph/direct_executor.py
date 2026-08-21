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
    """capability → tool_registry 节点名。

    fix f12：旧实现用字符串拼接 f"{prefix}_skill"，与注册表派生的
    真实节点名不一致（business.analyze → business_analysis_skill，
    拼接会得 business_skill → skill_not_found，direct 执行静默失败）。
    改用 CAPABILITY_MAP（单一事实来源），未注册时回退旧拼接逻辑。
    """
    node = tool_registry.get_node(capability)
    if node:
        return node
    parts = capability.split(".")
    if len(parts) == 2:
        return f"{parts[0]}_skill"
    return capability


def _failed_step(step_id: str, error_msg: str) -> dict:
    """fix f11：构造 failed 状态的 step_results，供 reporter 提示 + trace 留痕。"""
    return {
        step_id: {
            "step_id": step_id,
            "capability": "direct",
            "description": "直接执行",
            "status": "failed",
            "output": None,
            "error": error_msg,
            "retries": 0,
            "started_at": 0,
            "finished_at": 0,
        }
    }


# fix f13：需要前置数据输入的 capability。direct 单步执行时 previous_outputs
# 为空必失败（business.analyze 契约依赖 sql.query 的 SQLResult），
# 此处自动补前置步骤形成两段微编排（plan 模式由 Planner DAG 保证依赖）。
_PREDECESSOR_CAPS = {"business.analyze": "sql.query"}


def _run_skill_step(skill_nodes: dict, state: dict, step_id: str,
                    cap_name: str, description: str) -> dict:
    """执行单个 skill 节点，返回 step dict（含 status/output）。

    与 BaseSkill.execute() 的契约：state 需有 current_step_id 与
    plan.nodes[step_id]；skill 返回 {"step_results": {step_id: {...}}}。
    """
    step = {
        "step_id": step_id,
        "capability": cap_name,
        "description": description,
        "status": "running",
        "params": {"question": state.get("question", "")},
    }
    skill_func = skill_nodes[_extract_capability_name(cap_name)]
    result = asyncio.run(skill_func({
        **state,
        "step_id": step_id,
        "question": state.get("question", ""),
        "capability": cap_name,
        "params": step["params"],
        "step_results": state.get("step_results", {}),
        "current_step_id": step_id,
        "plan": {
            "nodes": {step_id: {
                "capability": cap_name,
                "description": description,
                "params": step["params"],
            }},
            "edges": {},
        },
    }))
    skill_output = result.get("step_results", {}).get(step_id, {})
    step["output"] = skill_output.get("output", str(result))
    step["status"] = skill_output.get("status", "success")
    if skill_output.get("error"):
        step["error"] = skill_output["error"]
    return step


def skill_executor_node(state: dict) -> dict:
    """direct mode: 跳过 Planner，直接调 candidates[0] 的 skill。

    路径:
      candidates[0] → tool_registry.get_skill_node(name) → 调 → 写 step_results
    """
    decision = state.get("route_decision") or {}
    candidates = decision.get("candidates", []) if isinstance(decision, dict) else []

    if not candidates:
        # 无 candidates → 固定边直达 reporter（skill_executor → reporter），
        # 由 Reporter 输出降级提示；executor_error 留痕供 trace 排查。
        # 注：route_selector 只在 router 出边生效，此处改 route_mode 不会
        # 重新路由到 planner。
        logger.warning("[SkillExecutor] 无 candidates，交 Reporter 降级提示")
        # fix f11：补 failed step_results，否则 reporter 收到空输入，
        # 提示笼统且 trace 无失败步骤留痕。
        return {
            **state,
            "executor_error": "no_candidates",
            "step_results": _failed_step("direct_1", "路由未找到可执行能力"),
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
            f"[SkillExecutor] skill 节点 {node_name} 不存在，交 Reporter 降级提示"
        )
        # fix f11：补 failed step_results（同上）
        return {
            **state,
            "executor_error": f"skill_not_found:{node_name}",
            "step_results": _failed_step("direct_1", f"能力 {cap_name} 暂不可用"),
        }

    # 构造 step（供 reporter 读取 + BaseSkill 需要 plan.nodes[step_id]）
    step_id = "direct_1"
    step_results: dict = {}

    # fix f13：business.analyze 等依赖型 capability 在 direct 模式缺前置输出，
    # 先自动补前置步骤（sql.query 拉数）再执行本体，避免必败。
    pre_cap = _PREDECESSOR_CAPS.get(cap_name)
    if pre_cap and not state.get("previous_outputs"):
        pre_node = _extract_capability_name(pre_cap)
        if pre_node in skill_nodes:
            logger.info(
                f"[SkillExecutor] direct: {cap_name} 自动补前置步骤 {pre_cap}"
            )
            try:
                pre_step = _run_skill_step(
                    skill_nodes, state, "direct_0", pre_cap,
                    f"自动补前置步骤 {pre_cap}",
                )
                step_results["direct_0"] = pre_step
                if pre_step["status"] == "success" and pre_step.get("output"):
                    state["previous_outputs"] = {
                        "direct_0": pre_step["output"],
                    }
                else:
                    logger.warning(
                        f"[SkillExecutor] 前置步骤 {pre_cap} 未产出数据，"
                        f"{cap_name} 将缺数据支撑"
                    )
            except Exception as e:
                logger.error(f"[SkillExecutor] 前置步骤 {pre_cap} 异常: {e}")
                step_results["direct_0"] = _failed_step("direct_0", str(e))["direct_0"]

    state["step_results"] = step_results
    # BaseSkill.execute() 要求 state 中有 current_step_id 和 plan.nodes[step_id]
    state["current_step_id"] = step_id
    state["plan"] = {
        "nodes": {step_id: {"capability": cap_name,
                            "description": f"直接执行 {cap_name}",
                            "params": {"question": state.get("question", "")}}},
        "edges": {},
    }

    # 调 skill（skill 是 async，用 asyncio.run）
    try:
        step = _run_skill_step(
            skill_nodes, {**state, "step_results": step_results},
            step_id, cap_name, f"直接执行 {cap_name}",
        )
        step_results[step_id] = step
        logger.info(f"[SkillExecutor] {cap_name} 完成: {len(str(step['output']))} chars")
    except Exception as e:
        step = {**_failed_step(step_id, str(e))[step_id], "capability": cap_name}
        step_results[step_id] = step
        logger.error(f"[SkillExecutor] {cap_name} 失败: {e}")

    return {
        **state,
        "step_results": step_results,
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
        logger.warning("[WorkflowExecutor] 无 workflow_name，交 Reporter 降级提示")
        # fix f11：补 failed step_results（同 skill_executor）
        return {
            **state,
            "executor_error": "no_workflow_name",
            "step_results": _failed_step("workflow_unknown", "未指定要运行的工作流"),
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
