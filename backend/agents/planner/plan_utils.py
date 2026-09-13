"""plan_utils.py — Planner 计划解析与规范化工具（无内部模块依赖）

设计动机（2026-09-13 循环导入根治）：
_extract_json / _normalize_plan 历史上定义在 planner.py，critique.py 又反向
导入它们；而 planner 经 orchestration 包 __init__ 间接依赖 graph → builder →
critique，形成 planner ↔ critique 循环导入（import 顺序敏感，先导 planner
即收集失败）。抽到独立模块后 critique → plan_utils，双向依赖消失。

依赖约束：本模块只允许依赖 shared/ + orchestration.tool_registry（轻量），
禁止 import planner / critique / orchestration.graph。
"""
from backend.observability.alerts import log_degradation, make_alert
from backend.orchestration.tool_registry import tool_registry
from backend.shared.logger import logger


def extract_json(text: str) -> dict:
    """4 层修复管道（实现见 shared/json_extractor.py）。

    全失败返回空 dict（触发 _fallback_plan），并记录降级告警。
    """
    from backend.shared.json_extractor import extract_json_or_empty

    result = extract_json_or_empty(text)
    if not result:
        logger.warning("[Planner] JSON 修复管道全部失败，触发兜底")
        alert = make_alert("PLAN_JSON_INVALID", {"text_preview": text[:200]})
        log_degradation(alert)
    return result


def normalize_plan(plan: dict) -> dict:
    """校验并规范化 plan 结构"""
    valid_capabilities = set(tool_registry.get_available_capabilities())

    raw_nodes = plan.get("nodes", [])
    nodes = {}

    if isinstance(raw_nodes, list):
        for node in raw_nodes:
            sid = str(node.get("step_id", ""))
            capability = node.get("capability", "")
            if capability not in valid_capabilities:
                logger.warning(f"[Planner] 无效 capability '{capability}' (step={sid})，跳过")
                continue
            nodes[sid] = {
                "step_id": sid,
                "capability": capability,
                "description": node.get("description", ""),
                "params": node.get("params", {}),
            }
    elif isinstance(raw_nodes, dict):
        for sid, node in raw_nodes.items():
            capability = node.get("capability", "")
            if capability not in valid_capabilities:
                logger.warning(f"[Planner] 无效 capability '{capability}' (step={sid})，跳过")
                continue
            nodes[str(sid)] = {
                "step_id": str(sid),
                "capability": capability,
                "description": node.get("description", ""),
                "params": node.get("params", {}),
            }

    # 规范化 edges
    raw_edges = plan.get("edges", {})
    edges = {}
    if isinstance(raw_edges, dict):
        for key, deps in raw_edges.items():
            if isinstance(deps, str):
                deps = [deps]
            elif not isinstance(deps, list):
                deps = []
            deps = [str(d) for d in deps]
            edges[str(key)] = deps

    return {"nodes": nodes, "edges": edges}
