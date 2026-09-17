"""SSE 事件构建器 — 从 system.py 抽出（PR-2.x 分解 MultiAgentSystem god class）。

所有函数纯函数/静态方法，无状态，可独立测试。
"""
import re
import time
from typing import Generator, Optional


def _capability_owner(cap: str) -> Optional[str]:
    """capability → 属主 Skill 节点名（f"{name}_skill"）；未知返回 None。

    不走 tool_registry.CAPABILITY_MAP——它是 cached_property，若在本模块
    被访问时 skills 尚未加载，会把空映射永久缓存，污染 Planner 能力清单。
    这里直接读底层注册表，不触发缓存。
    """
    if not cap:
        return None
    try:
        from backend.orchestration.capability_registry import tool_registry
        inst = tool_registry._get_skill_registry().get(cap)
        return f"{inst.name}_skill" if inst else None
    except Exception:  # noqa: BLE001 — 注册表不可用时不过滤（向后兼容）
        return None


# =====================================================
# P1: todo 快照 + 流中用量
# =====================================================

_TODO_STATUS_MAP = {"success": "completed", "failed": "failed", "skipped": "skipped"}


def make_todo_event(plan_nodes: dict, step_results: dict) -> dict:
    """构建 todo 事件 —— 任务列表全量快照。

    plan_nodes: plan["nodes"]（{step_id: {description, ...}}）
    step_results: 累积步骤结果（{step_id: {status, ...}}）
    全量快照而非增量：任务数有限，全量让前端无脑替换即可，避免乱序丢更新。
    """
    items = []
    for sid, node in plan_nodes.items():
        desc = node.get("description", "") if isinstance(node, dict) else str(node)
        raw = step_results.get(sid, {}).get("status", "pending")
        status = _TODO_STATUS_MAP.get(raw, "pending" if raw == "pending" else "in_progress")
        items.append({"id": sid, "text": desc or sid, "status": status})
    return {"event": "todo", "data": {"items": items, "ts": time.time()}}


def make_usage_event() -> Optional[dict]:
    """构建流中 usage 事件（当前轮次累计），无记录时返回 None。"""
    usage = summarize_turn_usage()
    if not usage:
        return None
    return {"event": "usage", "data": {**usage, "ts": time.time()}}


# =====================================================
# P1: file 事件 —— 工具落盘文件提取
# =====================================================

# 绝对路径 + 常见数据/文档后缀（export_csv 等工具产出；避免误匹配 URL 查询串）。
# (?<![A-Za-z]) 排除 URL scheme：https://x.com/a.csv 里的 "s:/x.com/a.csv"
# 恰好长得像盘符路径，前向一个字母即可区分（盘符前必是空白/行首/引号）。
_FILE_PATH_RE = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/][^\s\"']+?\.(?:csv|xlsx|xls|md|json|txt|py)\b")
# dict 输出中承载文件路径的常见 key
_FILE_PATH_KEYS = ("file_path", "filepath", "export_path", "path", "output")


def _extract_file_paths(output) -> list[str]:
    """从工具 step 输出中提取落盘文件路径（去重保序）。

    支持：dict（命中 _FILE_PATH_KEYS 的值 + output/output_preview 文本）、str（全文正则）。
    """
    paths: list[str] = []
    if isinstance(output, dict):
        for k in _FILE_PATH_KEYS:
            v = output.get(k)
            if isinstance(v, str):
                paths.extend(m.group(0) for m in _FILE_PATH_RE.finditer(v))
    elif isinstance(output, str):
        paths.extend(m.group(0) for m in _FILE_PATH_RE.finditer(output))
    return list(dict.fromkeys(paths))


def make_file_event(node_name: str, step_id: str, output) -> Optional[dict]:
    """构建 file 事件 —— 该步骤产出的文件清单，无文件时返回 None。"""
    files = _extract_file_paths(output)
    if not files:
        return None
    return {
        "event": "file",
        "data": {"node": node_name, "step_id": step_id, "files": files, "ts": time.time()},
    }


def stream_node_events(node_name: str, node_output: dict, skill_nodes: set,
                       make_step_payload, make_step_log_event) -> Generator[dict, None, None]:
    """根据节点名分派到对应的事件构建器。"""
    if node_name == "planner":
        yield from _build_planner_events(node_output)
    elif node_name == "critique":
        yield from _build_critique_events(node_output)
    elif node_name == "supervisor":
        yield from _build_supervisor_events(node_output, make_step_log_event)
    elif node_name == "tool_selector":
        yield from _build_tool_selector_events(node_output)
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


def _build_tool_selector_events(output: dict) -> Generator[dict, None, None]:
    """tool_selector 的 FC 选择结果事件。

    只在 FC 真正介入时发声（fc / no_match）；fast_path / passthrough
    直通不发——否则每条 direct 查询都多一条无信息量的日志。
    """
    sel = output.get("_tool_selection") or {}
    source = sel.get("source", "")
    if source == "fc":
        cap = sel.get("capability", "")
        message = (f"工具选择: {cap}（{len(sel.get('candidates', []))} 个候选，"
                   f"第 {sel.get('attempts', 1)} 次尝试，{sel.get('elapsed_ms', 0)}ms）")
        level = "info"
    elif source == "no_match":
        message = "未匹配到合适工具，回退默认执行"
        level = "warn"
    else:
        return
    yield {
        "event": "log", "data": {
            "level": level, "node": "tool_selector", "step_id": "tool_selection",
            "message": message,
            "payload": {
                "source": source,
                "capability": sel.get("capability"),
                "candidates": sel.get("candidates", []),
                "params": sel.get("params"),
                "elapsed_ms": sel.get("elapsed_ms"),
            },
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

        # 归属过滤（实测 2026-09-15 整改）：并行 Send 时每个 Skill 分支的
        # state 快照都携带其他步骤（BaseSkill.execute 返回全量 step_results），
        # 旧实现把整个 dict 全部打上本节点名 → step1 的失败挂在 rag_skill 名下、
        # 同一完成事件被两个 skill 各发一遍。现在按 capability 反查属主节点，
        # 只发本节点拥有的步骤；非终态（pending/running）是并行分支的快照
        # 噪声，也不发（进度由 supervisor 事件负责）。
        cap = sr.get("capability", "")
        owner = _capability_owner(cap)
        if owner is not None and owner != node_name:
            continue
        if status in ("pending", "running"):
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

        status_label = {"success": "完成", "failed": "失败", "skipped": "跳过"}.get(status, status)
        yield {
            "event": "log", "data": {
                "level": level, "node": node_name, "step_id": sid,
                "message": f"{status_label}: {desc}",
                "payload": payload, "ts": time.time(),
            },
        }

        # P1: 工具落盘文件（如 export_csv）→ file 事件，前端展示产出文件清单
        file_evt = make_file_event(node_name, sid, output_val)
        if file_evt:
            yield file_evt


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
    """将最终回答作为单个 delta 一次性产出（无真流式路径的兜底呈现）。

    P1 之前这里按句切开 + sleep(0.02) 模拟打字机（假流式）。真 token 级
    流式上线后，本函数仅剩两类调用方：Guard 短路话术、未发生 LLM 生成的
    兜底路径（如答案缓存命中/纯模板/降级回答）。这些场景没有"边生成边
    出字"可言，一次性发送整段即可，不再人为延迟。
    内容剥离 <!--META...--> 机读尾部（真流式路径由 MetaStreamFilter 处理）。
    """
    import re
    if not final_answer:
        return
    text = re.sub(r"<!--META.*?-->\s*", "", final_answer, flags=re.DOTALL).strip()
    if not text:
        return
    if stop_event is not None and stop_event.is_set():
        yield {"event": "error", "data": {"message": "用户中止", "ts": time.time()}}
        return
    yield {"event": "delta", "data": {"content": text, "ts": time.time()}}


def make_done_event(final_answer: str, all_step_results: dict, start_time: float,
                    usage: dict | None = None) -> dict:
    """构建 done 事件，附带耗时 + 引用来源 + 本轮 token 用量。"""
    from backend.agents.reporter.reporter import _extract_sources_from_steps
    from backend.agents.reporter.context_filter import parse_sources_from_text
    elapsed = time.time() - start_time
    sources = _extract_sources_from_steps(all_step_results)
    if not sources and final_answer:
        sources = parse_sources_from_text(final_answer)
    data: dict = {"elapsed": round(elapsed, 1), "sources": sources}
    if usage:
        data["usage"] = usage
    return {"event": "done", "data": data}


def summarize_turn_usage() -> dict | None:
    """汇总本轮（当前调用上下文）LLM token 用量，供 done 事件透出给前端。

    数据源：proxy 的 per-turn 累加器（每次 llm.invoke/ainvoke 累加）。
    无任何记录时返回 None（前端不显示用量行）。
    """
    try:
        from backend.infra.llm.proxy import get_turn_usage
        turn = get_turn_usage()
        if not turn:
            return None
        summary = {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            "cached_tokens": 0, "reasoning_tokens": 0, "cost_usd": 0.0,
            "calls": 0,
        }
        models = {}
        for model, e in turn.items():
            for k in ("prompt_tokens", "completion_tokens", "total_tokens",
                      "cached_tokens", "reasoning_tokens", "calls"):
                summary[k] += int(e.get(k, 0) or 0)
            summary["cost_usd"] += float(e.get("cost_usd", 0) or 0.0)
            models[model] = {
                "prompt_tokens": int(e.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(e.get("completion_tokens", 0) or 0),
                "total_tokens": int(e.get("total_tokens", 0) or 0),
                "calls": int(e.get("calls", 0) or 0),
            }
        summary["cost_usd"] = round(summary["cost_usd"], 6)
        summary["models"] = models
        return summary
    except Exception:
        return None


# =====================================================
# 共享辅助（纯函数）
# =====================================================

def make_initial_state(question: str, session_id: str, kb_id: str, messages: list,
                       guard_result: dict | None = None,
                       user_id: str = "", department: str = "") -> dict:
    """构建初始 AgentState。

    guard_result: Input Guard 判定结果（允许/降级放行时携带，
    供下游及后续 Tool Guard 读取风险标注；None = Guard 未启用）。
    user_id/department: 请求身份平铺进 state（P3 CS 断链修复）——
    CS 域（cs_prefilter/cs_graph_node/experts/*）直接读 state["user_id"]，
    此前不注入导致客服域恒为 anonymous。
    session_id: 同批平铺（2026-09-17 补漏）——cs_prefilter 此前
    state.get("session_id", "default") 恒取兜底，转人工工单
    conversation_id 全部挤在 "default"，坐席无法按真实会话认领。
    """
    return {
        "question": question.strip(),
        "kb_id": kb_id,
        "session_id": session_id,
        "user_id": user_id or "",
        "department": department or "",
        "plan": {"nodes": {}, "edges": {}},
        "step_results": {},
        "current_step_id": None,
        "messages": list(messages),
        "final_answer": "",
        "alerts": [],
        "guard_result": guard_result or {},
        "resolved_params": None,
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
