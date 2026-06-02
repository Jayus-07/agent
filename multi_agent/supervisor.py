"""
supervisor.py — Supervisor 节点 + 路由函数

职责分离:
  supervisor_node:     调度逻辑，状态更新，永远返回 dict
  route_after_supervisor: 路由函数，返回 list[Send] 或 "reporter"

Send API: 路由函数返回 list[Send] 时，LangGraph 自动并行执行所有目标节点，
Worker 完成后回到 supervisor_node，形成自然 loop。
"""

from langgraph.types import Send

from multi_agent.tool_registry import tool_registry
from utils.logger import logger


def supervisor_node(state: dict) -> dict:
    """
    调度节点：更新步骤状态，找出可以执行的 ready 步骤。

    永远返回 dict（state 更新），不返回 Send。
    Send 由 route_after_supervisor 负责。
    """
    plan = state.get("plan", {})
    nodes = plan.get("nodes", {})
    edges = plan.get("edges", {})
    step_results = dict(state.get("step_results", {}))

    if not nodes:
        logger.info("[Supervisor] 空 plan，完成")
        return {"_all_steps_done": True, "step_results": step_results}

    new_results = dict(step_results)
    ready_dispatch = []  # 收集就绪的步骤信息，由路由函数转为 Send

    for step_id, node_info in nodes.items():
        sr = new_results.get(step_id, {})
        status = sr.get("status", "pending")

        if status != "pending":
            continue

        # 检查依赖
        deps = edges.get(step_id, [])
        dep_failed = any(
            new_results.get(d, {}).get("status") == "failed"
            for d in deps
        )
        deps_met = all(
            new_results.get(d, {}).get("status") == "success"
            for d in deps
        )

        if dep_failed:
            new_results[step_id] = {
                "step_id": step_id,
                "capability": node_info.get("capability", ""),
                "description": node_info.get("description", ""),
                "status": "skipped",
                "output": None,
                "error": "前置步骤执行失败",
                "retries": 0,
                "started_at": 0,
                "finished_at": 0,
            }
            logger.warning(f"[Supervisor] step={step_id} 因前置失败被跳过")
            continue

        if deps_met:
            capability = node_info.get("capability", "")
            worker_name = tool_registry.get_worker(capability)

            if worker_name:
                ready_dispatch.append({
                    "worker": worker_name,
                    "step_id": step_id,
                })
                logger.info(f"[Supervisor] 就绪: step={step_id} "
                            f"cap={capability} → {worker_name}")
            else:
                new_results[step_id] = {
                    "step_id": step_id,
                    "capability": capability,
                    "description": node_info.get("description", ""),
                    "status": "failed",
                    "output": None,
                    "error": f"未注册的 capability: {capability}",
                    "retries": 0,
                    "started_at": 0,
                    "finished_at": 0,
                }
                logger.warning(f"[Supervisor] step={step_id} capability 无效: {capability}")

    # 判断是否全部结束
    all_done = all(
        new_results.get(sid, {}).get("status") in ("success", "failed", "skipped")
        for sid in nodes
    )

    if not ready_dispatch:
        if all_done:
            success_count = sum(
                1 for sid in nodes
                if new_results.get(sid, {}).get("status") == "success"
            )
            logger.info(f"[Supervisor] 全部完成: {success_count}/{len(nodes)} 成功")
            return {
                "_all_steps_done": True,
                "_ready_dispatch": [],
                "step_results": new_results,
            }
        else:
            running = [
                sid for sid in nodes
                if new_results.get(sid, {}).get("status") == "running"
            ]
            logger.info(f"[Supervisor] 等待 Worker 返回: {running}")
            return {
                "_all_steps_done": False,
                "_ready_dispatch": [],
                "step_results": new_results,
            }

    # 有就绪步骤
    return {
        "_all_steps_done": False,
        "_ready_dispatch": ready_dispatch,
        "step_results": new_results,
    }


def route_after_supervisor(state: dict) -> str | list:
    """
    路由函数（conditional_edges 的回调）。

    返回 list[Send] → LangGraph 并行执行所有 Worker。
    返回 "reporter"   → 进入 Reporter 汇总。

    注意：这不是节点函数，而是 add_conditional_edges 的路径选择函数。
    """
    ready = state.get("_ready_dispatch", [])

    if ready:
        sends = []
        for item in ready:
            # Send 的 arg 是目标节点的完整输入 state，需传递全部字段
            sends.append(
                Send(item["worker"], {
                    "question": state.get("question", ""),
                    "plan": state.get("plan", {}),
                    "step_results": state.get("step_results", {}),
                    "current_step_id": item["step_id"],
                    "messages": state.get("messages", []),
                    "final_answer": state.get("final_answer", ""),
                    "_all_steps_done": False,
                    "_ready_dispatch": [],
                })
            )
        return sends

    return "reporter"
