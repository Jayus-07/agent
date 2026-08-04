"""SSE 事件构建器 — 从 system.py 抽出（PR-2.x 分解 MultiAgentSystem god class）。

所有函数纯函数/静态方法，无状态，可独立测试。
"""
import re
import time
from typing import Generator, Optional


def stream_node_events(node_name: str, node_output: dict, skill_nodes: set,
                       make_step_payload, make_step_log_event) -> Generator[dict, None, None]:
    """根据节点名分派到对应的事件构建器。"""
    if node_name == "planner":
        yield from _build_planner_events(node_output)
    elif node_name == "critique":
        yield from _build_critique_events(node_output)
    elif node_name == "supervisor":
        yield from _build_supervisor_events(node_output, make_step_log_event)
    elif node_name == "reporter":
        yield from _build_reporter_events(node_output)
    elif node_name in skill_nodes:
        yield from _build_skill_events(node_name, node_output, make_step_payload)


def _build_planner_events(output: dict) -> Generator[dict, None, None]:
    plan = output.get("plan", {})
    nodes = plan.get("nodes", {})
    descriptions = [n.get("description", "") for n in nodes.values()]
    yield {
        "event": "log", "data": {
            "level": "info", "node": "planner", "step_id": "planning",
            "message": f"任务分解完成，共 {len(nodes)} 个子任务",
            "payload": {"task_count": len(nodes), "tasks": descriptions},
            "ts": time.time(),
        },
    }


def _build_critique_events(output: dict) -> Generator[dict, None, None]:
    plan_changed = output.get("_plan_changed", False)
    plan = output.get("plan", {})
    nodes = plan.get("nodes", {})
    yield {
        "event": "log", "data": {
            "level": "warn" if plan_changed else "info",
            "node": "critique", "step_id": "critique",
            "message": f"计划已修正，共 {len(nodes)} 个步骤" if plan_changed
                       else "计划审查通过，无需修正",
            "payload": {"plan_changed": plan_changed, "task_count": len(nodes)},
            "ts": time.time(),
        },
    }


def _build_supervisor_events(output: dict, make_step_log_event) -> Generator[dict, None, None]:
    step_results = output.get("step_results", {})

    for sid, sr in step_results.items():
        status = sr.get("status", "?")
        if status == "?":
            continue
        desc = sr.get("description", sid)
        yield make_step_log_event("supervisor", sid, status, desc, sr)

    ready = output.get("_ready_dispatch", [])
    if ready:
        dispatch_info = [{"step_id": r["step_id"], "skill": r["worker"]} for r in ready]
        yield {
            "event": "log", "data": {
                "level": "info", "node": "supervisor", "step_id": "dispatch",
                "message": f"调度 {len(ready)} 个任务到 Skill",
                "payload": {"dispatched": dispatch_info}, "ts": time.time(),
            },
        }


def _build_skill_events(node_name: str, output: dict, make_step_payload) -> Generator[dict, None, None]:
    step_results = output.get("step_results", {})
    for sid, sr in step_results.items():
        status = sr.get("status", "?")
        if status == "?":
            continue
        desc = sr.get("description", sid)
        output_val = sr.get("output", "")
        payload = make_step_payload(sr, include_output=True)
        if isinstance(output_val, dict):
            for k in ("sql", "query", "params", "row_count", "result_count", "top_k"):
                if k in output_val:
                    payload[k] = output_val[k]

        level = "info"
        if status == "failed":
            level = "error"
        elif status == "skipped":
            level = "warn"

        yield {
            "event": "log", "data": {
                "level": level, "node": node_name, "step_id": sid,
                "message": f"{'完成' if status == 'success' else '失败'}: {desc}",
                "payload": payload, "ts": time.time(),
            },
        }


def _build_reporter_events(output: dict) -> Generator[dict, None, None]:
    final_answer = output.get("final_answer", "")
    yield {
        "event": "log", "data": {
            "level": "info", "node": "reporter", "step_id": "summary",
            "message": f"汇总生成最终回答 ({len(final_answer)} 字符)",
            "payload": {"char_count": len(final_answer)}, "ts": time.time(),
        },
    }


# =====================================================
# 流式输出：delta + done
# =====================================================

def emit_delta_events(final_answer: str, stop_event=None) -> Generator[dict, None, None]:
    """将最终回答按句子切分，逐句产出 delta 事件（打字机效果）。"""
    if not final_answer:
        return
    sentences = re.split(r'(?<=[。！？\n])', final_answer)
    sentences = [s for s in sentences if s.strip()]
    for sentence in sentences:
        if stop_event is not None and stop_event.is_set():
            yield {"event": "error", "data": {"message": "用户中止", "ts": time.time()}}
            return
        yield {"event": "delta", "data": {"content": sentence, "ts": time.time()}}
        time.sleep(0.02)


def make_done_event(final_answer: str, all_step_results: dict, start_time: float) -> dict:
    """构建 done 事件，附带耗时 + 引用来源。"""
    from backend.agents.reporter.reporter import _extract_sources_from_steps
    from backend.agents.reporter.context_filter import parse_sources_from_text
    elapsed = time.time() - start_time
    sources = _extract_sources_from_steps(all_step_results)
    if not sources and final_answer:
        sources = parse_sources_from_text(final_answer)
    return {"event": "done", "data": {"elapsed": round(elapsed, 1), "sources": sources}}


# =====================================================
# 共享辅助（纯函数）
# =====================================================

def make_initial_state(question: str, session_id: str, kb_id: str, messages: list) -> dict:
    """构建初始 AgentState。"""
    return {
        "question": question.strip(),
        "kb_id": kb_id,
        "plan": {"nodes": {}, "edges": {}},
        "step_results": {},
        "current_step_id": None,
        "messages": list(messages),
        "final_answer": "",
        "alerts": [],
        "_supervisor_loop_count": 0,
        "_plan_critiqued": False,
        "_plan_changed": False,
    }


def extract_sources_from_results(step_results: dict, final_answer: str) -> list[dict]:
    """从 step_results 提取引用来源。"""
    from backend.agents.reporter.reporter import _extract_sources_from_steps
    from backend.agents.reporter.context_filter import parse_sources_from_text
    sources = _extract_sources_from_steps(step_results)
    if not sources and final_answer:
        sources = parse_sources_from_text(final_answer)
    return sources


def make_step_payload(sr: dict, include_output: bool = False) -> dict:
    """从 step result 提取通用 payload 字段。"""
    payload: dict = {
        "description": sr.get("description", ""),
        "status": sr.get("status", "?"),
    }
    if include_output:
        output = sr.get("output", "")
        payload["output_preview"] = str(output)[:500] if output else ""
    if sr.get("error"):
        payload["error"] = sr["error"]
    if sr.get("started_at") is not None:
        payload["started_at"] = round(sr["started_at"], 3)
    if sr.get("finished_at") is not None:
        payload["elapsed"] = round(sr["finished_at"] - sr.get("started_at", sr["finished_at"]), 2)
        payload["finished_at"] = round(sr["finished_at"], 3)
    return payload


def make_step_log_event(node_name: str, step_id: str, status: str,
                          desc: str, sr: dict) -> dict:
    """构建单个步骤的 log 事件。"""
    level = "info"
    if status == "failed":
        level = "error"
    elif status == "skipped":
        level = "warn"

    status_label = {
        "success": "完成", "failed": "失败", "skipped": "跳过", "pending": "派发",
    }.get(status, status)

    return {
        "event": "log", "data": {
            "level": level, "node": node_name, "step_id": step_id,
            "message": f"{status_label}: {desc}",
            "payload": make_step_payload(sr),
            "ts": time.time(),
        },
    }
