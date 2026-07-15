"""
scheduler.py — Supervisor 节点 + 路由函数

职责分离:
  supervisor_node:     调度逻辑，状态更新，永远返回 dict
  route_after_supervisor: 路由函数，返回 list[Send] 或 "reporter"

Send API: 路由函数返回 list[Send] 时，LangGraph 自动并行执行所有目标 Skill，
Skill 完成后回到 supervisor_node，形成自然 loop。
"""

import time

from langgraph.types import Send

from backend.agent.tool_registry import tool_registry
from backend.agent.supervisor.degradation import execute_degradation
from backend.agent.supervisor.alerts import make_alert, log_degradation
from backend.utils.logger import logger


MAX_SUPERVISOR_LOOPS = 10


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

    # 循环上限检查
    loop_count = state.get("_supervisor_loop_count", 0)
    if loop_count >= MAX_SUPERVISOR_LOOPS:
        logger.error(f"[Supervisor] 达到最大循环次数 {MAX_SUPERVISOR_LOOPS}，强制终止")
        alert = make_alert("SUPERVISOR_MAX_LOOP", {
            "loop_count": loop_count, "nodes": list(nodes.keys()),
        })
        log_degradation(alert)

        for sid, node_info in nodes.items():
            sr = step_results.get(sid, {})
            if sr.get("status") in ("pending", "running"):
                step_results[sid] = {
                    "step_id": sid,
                    "capability": node_info.get("capability", ""),
                    "description": node_info.get("description", ""),
                    "status": "failed",
                    "output": None,
                    "error": f"超出最大调度轮次（{MAX_SUPERVISOR_LOOPS}）",
                    "error_type": "timeout",
                    "retries": 0,
                    "started_at": 0,
                    "finished_at": 0,
                }

        alerts = state.get("alerts", [])
        return {
            "_all_steps_done": True, "_ready_dispatch": [],
            "step_results": step_results, "_supervisor_loop_count": loop_count,
            "alerts": alerts + [make_alert("SUPERVISOR_MAX_LOOP", {})],
        }

    if not nodes:
        logger.info("[Supervisor] 空 plan，完成")
        return {
            "_all_steps_done": True, "_ready_dispatch": [],
            "step_results": step_results, "_supervisor_loop_count": loop_count,
        }

    new_results = dict(step_results)
    ready_dispatch = []
    degraded_steps = state.get("_degraded_steps", set())

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
                "status": "skipped", "output": None,
                "error": "前置步骤执行失败",
                "error_type": None, "retries": 0,
                "started_at": 0, "finished_at": 0,
            }
            logger.warning(f"[Supervisor] step={step_id} 因前置失败被跳过")
            continue

        if deps_met:
            capability = node_info.get("capability", "")
            skill_name = tool_registry.get_worker(capability)

            if skill_name:
                new_results[step_id] = {
                    "step_id": step_id, "capability": capability,
                    "description": node_info.get("description", ""),
                    "status": "running", "started_at": time.time(),
                }
                ready_dispatch.append({"worker": skill_name, "step_id": step_id})
                logger.info(f"[Supervisor] 就绪: step={step_id} cap={capability} → {skill_name}")
            else:
                new_results[step_id] = {
                    "step_id": step_id, "capability": capability,
                    "description": node_info.get("description", ""),
                    "status": "failed", "output": None,
                    "error": f"未注册的 capability: {capability}",
                    "error_type": None, "retries": 0,
                    "started_at": 0, "finished_at": 0,
                }
                logger.warning(f"[Supervisor] step={step_id} capability 无效: {capability}")

    # 判断是否全部结束
    all_done = all(
        new_results.get(sid, {}).get("status") in ("success", "failed", "skipped")
        for sid in nodes
    )

    if not ready_dispatch:
        if all_done:
            question = state.get("question", "")
            new_results, ready_dispatch, degraded_steps = execute_degradation(
                nodes, edges, new_results, ready_dispatch,
                degraded_steps, question,
            )

            if ready_dispatch:
                return {
                    "_all_steps_done": False, "_ready_dispatch": ready_dispatch,
                    "step_results": new_results, "_supervisor_loop_count": loop_count + 1,
                    "_degraded_steps": degraded_steps,
                    "plan": plan,
                }

            success_count = sum(
                1 for sid in nodes
                if new_results.get(sid, {}).get("status") == "success"
            )
            logger.info(f"[Supervisor] 全部完成: {success_count}/{len(nodes)} 成功")
            return {
                "_all_steps_done": True, "_ready_dispatch": [],
                "step_results": new_results, "_supervisor_loop_count": loop_count + 1,
                "_degraded_steps": degraded_steps,
            }
        else:
            running = [
                sid for sid in nodes
                if new_results.get(sid, {}).get("status") == "running"
            ]
            logger.info(f"[Supervisor] 等待 Skill 返回: {running}")
            return {
                "_all_steps_done": False, "_ready_dispatch": [],
                "step_results": new_results, "_supervisor_loop_count": loop_count + 1,
                "_degraded_steps": degraded_steps,
            }

    return {
        "_all_steps_done": False, "_ready_dispatch": ready_dispatch,
        "step_results": new_results, "_supervisor_loop_count": loop_count + 1,
        "_degraded_steps": degraded_steps,
    }


def route_after_supervisor(state: dict) -> str | list:
    """
    路由函数（conditional_edges 的回调）。

    返回 list[Send] → LangGraph 并行执行所有 Skill。
    返回 "reporter"   → 进入 Reporter 汇总。
    """
    ready = state.get("_ready_dispatch", [])

    if ready:
        sends = []
        for item in ready:
            degraded = frozenset(state.get("_degraded_steps", set()) or set())
            sends.append(
                Send(item["worker"], {
                    "question": state.get("question", ""),
                    "kb_id": state.get("kb_id", "default"),
                    "plan": state.get("plan", {}),
                    "step_results": state.get("step_results", {}),
                    "current_step_id": item["step_id"],
                    "messages": state.get("messages", []),
                    "final_answer": state.get("final_answer", ""),
                    "alerts": state.get("alerts", []),
                    "_all_steps_done": False,
                    "_ready_dispatch": [],
                    "_supervisor_loop_count": state.get("_supervisor_loop_count", 0),
                    "_degraded_steps": degraded,
                })
            )
        return sends

    return "reporter"
