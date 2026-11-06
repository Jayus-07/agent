"""plan_utils.py — Planner 计划解析与规范化工具（无内部模块依赖）

设计动机（2026-09-13 循环导入根治）：
_extract_json / _normalize_plan 历史上定义在 planner.py，critique.py 又反向
导入它们；而 planner 经 orchestration 包 __init__ 间接依赖 graph → builder →
critique，形成 planner ↔ critique 循环导入（import 顺序敏感，先导 planner
即收集失败）。抽到独立模块后 critique → plan_utils，双向依赖消失。

依赖约束：本模块只允许依赖 shared/ + 函数内延迟导入的
orchestration.router.manifest（与 critique 同模式，manifest 不反向依赖
本模块，无循环），禁止 import planner / critique / orchestration.graph。
"""
from backend.observability.alerts import log_degradation, make_alert
from backend.shared.logger import logger

# 计划依赖链（edges 连成的最长路径）允许的最大深度。
# 依据：supervisor 的 MAX_SUPERVISOR_LOOPS=10 是「全图循环轮次」——深度 d 的
# 串行链约消耗 d+1 轮调度（逐层派发 + 1 轮完成判定），再留降级重试余量，
# 8 是安全上限。超过它 supervisor 会在中途把剩余步骤全部判 failed（表现为
# 「莫名其妙全军覆没」），因此必须在进入 supervisor 之前拦截。
MAX_PLAN_DEPTH = 8


def plan_depth(plan: dict) -> int:
    """计算 plan 的依赖深度：edges 连成的最长路径上的节点数（纯函数）。

    无环假设下按最长路 DP；若检测到环（拓扑无法收敛），返回
    MAX_PLAN_DEPTH + 1（环本身就是不可调度的非法计划，直接超限）。

    plan 形如 {"nodes": {...}, "edges": {"3": ["1", "2"]}}，
    edges[k] = 步骤 k 依赖的步骤列表。
    """
    edges: dict[str, list[str]] = plan.get("edges", {}) or {}
    if not edges:
        return 0 if not plan.get("nodes") else 1

    # 记忆化最长路（以节点计）。iterative DFS + on-stack 环检测。
    depth_cache: dict[str, int] = {}
    on_stack: set[str] = set()
    has_cycle = False

    def longest(node: str) -> int:
        nonlocal has_cycle
        if node in depth_cache:
            return depth_cache[node]
        if node in on_stack:  # 环
            has_cycle = True
            return 0
        on_stack.add(node)
        deps = edges.get(node) or []
        best = 0
        for d in deps:
            best = max(best, longest(d))
        on_stack.discard(node)
        depth_cache[node] = best + 1
        return best + 1

    for sid in set(list(edges.keys()) + [d for ds in edges.values() for d in ds]):
        longest(str(sid))

    if has_cycle:
        return MAX_PLAN_DEPTH + 1
    return max(depth_cache.values()) if depth_cache else 0


def validate_plan_contract(
    plan: object,
    allowed: set[str] | None = None,
) -> list[str]:
    """验证 Planner 的原始 JSON 契约，不做静默修复或删步。

    模型输出和缓存计划在调用 ``normalize_plan`` 前使用此函数，避免越权
    capability、缺字段和非法依赖被规范化阶段掩盖。契约与 prompt 示例一致：
    nodes 是节点数组，edges 是「目标步骤 -> 依赖步骤数组」的映射。
    """
    errors: list[str] = []
    if not isinstance(plan, dict):
        return ["root must be an object"]

    expected_root = {"nodes", "edges"}
    actual_root = set(plan)
    if actual_root != expected_root:
        missing = expected_root - actual_root
        extra = actual_root - expected_root
        if missing:
            errors.append("root is missing required fields")
        if extra:
            errors.append("root contains unsupported fields")

    raw_nodes = plan.get("nodes")
    if not isinstance(raw_nodes, list):
        errors.append("nodes must be an array")
        raw_nodes = []

    node_fields = {"step_id", "capability", "description", "params"}
    nodes: dict[str, dict] = {}
    for index, raw_node in enumerate(raw_nodes):
        if not isinstance(raw_node, dict):
            errors.append(f"nodes[{index}] must be an object")
            continue
        fields = set(raw_node)
        if fields != node_fields:
            errors.append(f"nodes[{index}] has an invalid field set")

        step_id = raw_node.get("step_id")
        if (
            not isinstance(step_id, str)
            or not step_id
            or len(step_id) > 64
            or step_id.strip() != step_id
        ):
            errors.append(f"nodes[{index}].step_id must be a non-empty string")
            continue
        if step_id in nodes:
            errors.append(f"nodes[{index}].step_id is duplicated")
            continue

        capability = raw_node.get("capability")
        if (
            not isinstance(capability, str)
            or not capability.strip()
            or len(capability) > 128
        ):
            errors.append(f"nodes[{index}].capability must be a non-empty string")
        elif allowed is not None and capability not in allowed:
            errors.append(f"nodes[{index}].capability is not authorized")

        description = raw_node.get("description")
        if (
            not isinstance(description, str)
            or not description.strip()
            or len(description) > 2000
        ):
            errors.append(f"nodes[{index}].description must be a non-empty string")
        if not isinstance(raw_node.get("params"), dict):
            errors.append(f"nodes[{index}].params must be an object")
        nodes[step_id] = raw_node

    raw_edges = plan.get("edges")
    if not isinstance(raw_edges, dict):
        errors.append("edges must be an object")
        raw_edges = {}

    edges: dict[str, list[str]] = {}
    for target, raw_deps in raw_edges.items():
        if not isinstance(target, str) or target not in nodes:
            errors.append("edges contains an unknown target step")
            continue
        if not isinstance(raw_deps, list):
            errors.append("edge dependencies must be arrays")
            continue
        deps: list[str] = []
        for dependency in raw_deps:
            if not isinstance(dependency, str) or dependency not in nodes:
                errors.append("edges contains an unknown dependency step")
                continue
            if dependency == target:
                errors.append("a step cannot depend on itself")
            if dependency in deps:
                errors.append("an edge contains a duplicate dependency")
            else:
                deps.append(dependency)
        edges[target] = deps

    if not errors:
        depth = plan_depth({"nodes": nodes, "edges": edges})
        if depth > MAX_PLAN_DEPTH:
            errors.append("plan contains a cycle or exceeds the maximum depth")
    return errors


def extract_json(text: str, source: str = "planner.system") -> dict:
    """4 层修复管道（实现见 shared/json_extractor.py）。

    全失败返回空 dict（触发 _fallback_plan），并记录降级告警 +
    llm_json_parse_fail_total{source}（提示词/模型劣化最早信号）。
    """
    from backend.shared.json_extractor import extract_json_or_empty

    result = extract_json_or_empty(text, source=source)
    if not result:
        logger.warning("[Planner] JSON 修复管道全部失败，触发兜底")
        alert = make_alert("PLAN_JSON_INVALID", {"text_preview": text[:200]})
        log_degradation(alert)
    return result


def normalize_plan(plan: dict, allowed: set[str] | None = None) -> dict:
    """校验并规范化 plan 结构。

    有效能力集与 Planner 渲染、Critique 校验同源（effective_manifest：
    静态 manifest + MCP 动态 mcp.*）——此前取 tool_registry 静态
    CAPABILITY_MAP，动态 mcp.* 步骤会被静默删除（纯 MCP 计划变空计划）。

    `allowed` 非空时在 manifest 可见集之上叠加**当前请求的授权集**
    （product_allows_capability 裁决，Planner/Critique 传入）——LLM 修正
    结果不得保留当前产品/租户无权执行的能力（PR #115 Codex P2）；None =
    旧口径（仅 manifest 可见集，不过产品过滤）。
    """
    from backend.orchestration.router.manifest import effective_manifest

    valid_capabilities = set(effective_manifest().planner_visible_capability_names)
    if allowed is not None:
        valid_capabilities &= set(allowed)

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
