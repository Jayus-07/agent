"""
supervisor.py — Supervisor 节点 + 路由函数

职责分离:
  supervisor_node:     调度逻辑，状态更新，永远返回 dict
  route_after_supervisor: 路由函数，返回 list[Send] 或 "reporter"

Send API: 路由函数返回 list[Send] 时，LangGraph 自动并行执行所有目标节点，
Worker 完成后回到 supervisor_node，形成自然 loop。
"""

import time

from langgraph.types import Send

from multi_agent.tool_registry import tool_registry
from utils.logger import logger


# SQL 空结果关键词（中文 + 英文）
_SQL_EMPTY_PATTERNS = ["(无结果)", "无结果", "0 rows", "no results", "没有找到", "未找到相关", "暂无数据"]


def _check_sql_fallback(nodes: dict, edges: dict, step_results: dict, ready_dispatch: list):
    """SQL 查询返回空结果时，仅在问题涉及知识库时才自动添加 search_knowledge 降级步骤"""
    has_rag = any(
        n.get("capability") == "search_knowledge"
        for n in nodes.values()
    )
    if has_rag:
        return step_results, ready_dispatch, False

    # 找到返回空结果的 SQL 步骤
    empty_sql_step = None
    for sid, n in nodes.items():
        sr = step_results.get(sid, {})
        if sr.get("capability") != "query_database":
            continue
        if sr.get("status") != "success":
            continue
        output = str(sr.get("output", ""))
        if any(p in output for p in _SQL_EMPTY_PATTERNS):
            empty_sql_step = sid
            break

    if not empty_sql_step:
        return step_results, ready_dispatch, False

    # SQL 空结果 → 无条件触发 RAG 降级（数据库查不到就去知识库找）
    question = str(nodes[empty_sql_step].get("params", {}).get("question", ""))
    logger.info(
        f"[Supervisor] SQL step={empty_sql_step} 返回空，自动触发 RAG 降级"
    )

    # 动态插入 RAG 降级步骤
    fallback_id = f"{empty_sql_step}_rag_fallback"
    nodes[fallback_id] = {
        "step_id": fallback_id,
        "capability": "search_knowledge",
        "description": f"数据库无结果，知识库降级检索: {question[:30]}...",
        "params": {"question": question},
    }

    worker = tool_registry.get_worker("search_knowledge")
    if worker:
        logger.info(f"[Supervisor] SQL step={empty_sql_step} 返回空，降级到 RAG (step={fallback_id})")
        step_results[fallback_id] = {"status": "pending"}
        ready_dispatch.append({"worker": worker, "step_id": fallback_id})

    return step_results, ready_dispatch, True


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
                new_results[step_id] = {
                    "step_id": step_id,
                    "capability": capability,
                    "description": node_info.get("description", ""),
                    "status": "running",
                    "started_at": time.time(),
                }
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
            # ── SQL 空结果降级 → 自动添加 RAG 步骤 ──
            new_results, ready_dispatch, plan_modified = _check_sql_fallback(
                nodes, edges, new_results, ready_dispatch
            )
            if ready_dispatch:
                result = {
                    "_all_steps_done": False,
                    "_ready_dispatch": ready_dispatch,
                    "step_results": new_results,
                }
                if plan_modified:
                    result["plan"] = plan
                return result

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
                    "kb_id": state.get("kb_id", "default"),
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
