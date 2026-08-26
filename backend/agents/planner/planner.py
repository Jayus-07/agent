"""
planner.py — Planner 节点（P2 性能优化：LRU 缓存）

职责:
  - 理解用户问题
  - 拆解任务为子步骤
  - 生成 DAG 执行计划 (nodes + edges)
  - 不调用任何工具

Planner 只输出 capability，不指定具体 tool。
Tool 选择由 Supervisor + ToolRegistry 完成。

缓存策略（P2 perf）:
  - 相同问题 + 相同 capability 集合 → 5min TTL 缓存
  - LRU 淘汰，最多 64 条
  - 不同 kb_id 视为不同请求（不同知识库可能有不同 plan）
"""

import hashlib
import json
import threading
import time

from backend.infra.llm import llm
from backend.orchestration.tool_registry import tool_registry
from backend.observability.alerts import make_alert, log_degradation
from backend.prompts.planner import PLANNER_SYSTEM, is_knowledge_question
from backend.shared.logger import logger

# ── P2 性能优化：Planner 缓存 ──
_PLAN_CACHE: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 300  # 5 分钟
_CACHE_MAX = 64


def _cache_key(question: str, kb_id: str) -> str:
    """缓存键：问题 + KB ID 的 hash。"""
    caps = tuple(sorted(tool_registry.get_available_capabilities()))
    raw = f"{question}|{kb_id}|{caps}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _cache_get(question: str, kb_id: str) -> dict | None:
    key = _cache_key(question, kb_id)
    _cache_lock.acquire()
    try:
        now = time.time()
        expired = [k for k, v in _PLAN_CACHE.items() if now - v[0] > _CACHE_TTL]
        for k in expired:
            del _PLAN_CACHE[k]
        if key in _PLAN_CACHE:
            ts, plan = _PLAN_CACHE[key]
            if now - ts <= _CACHE_TTL:
                return plan
            del _PLAN_CACHE[key]
    finally:
        _cache_lock.release()
    return None


def _cache_set(question: str, kb_id: str, plan: dict) -> None:
    key = _cache_key(question, kb_id)
    _cache_lock.acquire()
    try:
        if len(_PLAN_CACHE) >= _CACHE_MAX:
            oldest = min(_PLAN_CACHE.items(), key=lambda x: x[1][0])
            del _PLAN_CACHE[oldest[0]]
        _PLAN_CACHE[key] = (time.time(), plan)
    finally:
        _cache_lock.release()


# =====================================================
# Planner 节点
# =====================================================

def _format_capabilities_schema() -> str:
    """格式化所有 capability 的 schema 为 Planner prompt 用。

    注：依赖 tool_registry 保留在 planner.py 而非 prompts/planner.py
    是为了避免循环导入（prompts 不能反向依赖 backend.agent）。
    """
    lines = []
    for cap_name in tool_registry.get_available_capabilities():
        schema = tool_registry.get_schema(cap_name)
        if not schema:
            continue
        lines.append(f"### {cap_name}")
        lines.append(f"描述: {schema['description']}")
        lines.append(f"参数: {json.dumps(schema['params'], ensure_ascii=False)}")
        if "示例" in schema:
            lines.append(f"示例: {json.dumps(schema['示例'], ensure_ascii=False)}")
        lines.append("")
    return "\n".join(lines)


def planner_node(state: dict) -> dict:
    """
    输入 state.question，输出 state.plan。

    产出:
        plan = {
            "nodes": {"1": {...}, "2": {...}},
            "edges": {"3": ["1", "2"]}
        }

    2026-08-11: 接收 Router candidates hint（state.route_decision.candidates）
    作为 hint 注入 prompt，让 LLM 倾向使用这些 capability。
    """
    question = state.get("question", "")
    kb_id = state.get("kb_id", "default")

    if not question:
        logger.warning("[Planner] 空问题，返回空 plan")
        return {"plan": {"nodes": {}, "edges": {}}}

    # ── 2026-08-11: Router candidates hint ──
    router_decision = state.get("route_decision") or {}
    router_candidates = router_decision.get("candidates", []) if isinstance(router_decision, dict) else []
    router_hint_text = ""
    if router_candidates:
        caps_text = ", ".join(
            f"{c['name']}({c['score']:.2f})" for c in router_candidates
        )
        router_hint_text = f"\n\n【Router 建议】以下能力可能相关（仅供参考，可扩展）：{caps_text}"

    # ── P2 性能优化：缓存命中 → 跳过 LLM ──
    cached = _cache_get(question, kb_id)
    if cached is not None:
        logger.info(f"[Planner] 缓存命中 → {len(cached.get('nodes',{}))} 节点")
        return {"plan": cached}

    capabilities_schema = _format_capabilities_schema()
    cap_example = tool_registry.get_available_capabilities()[0]

    prompt = PLANNER_SYSTEM.format(
        capabilities_schema=capabilities_schema,
        cap_example=cap_example,
    )
    user_msg = f"用户问题: {question}{router_hint_text}\n\n请输出 JSON:"

    logger.info(f"[Planner] 分析问题: {question[:80]}...")

    try:
        resp = llm.invoke([
            ("system", prompt),
            ("human", user_msg),
        ])
        content = resp.content.strip()

        # 提取 JSON（现在直接返回 dict）
        plan = _extract_json(content)

        # 校验
        plan = _normalize_plan(plan)

        # 后置规则过滤器：非知识类问题强制移除冗余 RAG 步骤
        plan = _filter_plan(plan, question)

        # 知识类问题强制补 RAG：如果问题含知识库关键词但 Planner 未创建 RAG 步骤
        plan = _ensure_knowledge_step(plan, question)

        node_count = len(plan.get("nodes", {}))
        edge_count = len(plan.get("edges", {}))
        logger.info(f"[Planner] 计划生成: {node_count} 个节点, {edge_count} 条依赖")

        # P2 perf: 写入缓存
        _cache_set(question, kb_id, plan)

        # 兜底：空计划 → 自动添加 search_knowledge 步骤
        if not plan.get("nodes"):
            plan = _fallback_plan(question)
            logger.info(f"[Planner] 空计划，使用兜底 RAG 步骤")

        # KB 隔离：为所有 search_knowledge 步骤注入 kb_id
        for step_id, node in plan.get("nodes", {}).items():
            if node.get("capability") == "rag.search":
                node.setdefault("params", {})
                node["params"]["kb_id"] = kb_id

        return {"plan": plan}

    except json.JSONDecodeError as e:
        logger.error(f"[Planner] JSON 解析失败: {e}, 内容: {content[:200]}")
        logger.info("[Planner] 使用兜底 RAG 步骤")
        return {"plan": _fallback_plan(question)}
    except Exception as e:
        logger.error(f"[Planner] 规划失败: {e}")
        logger.info("[Planner] 使用兜底 RAG 步骤")
        return {"plan": _fallback_plan(question)}


# =====================================================
# JSON 提取（P1-14：统一收敛到 backend.shared.json_extractor）
# =====================================================

def _extract_json(text: str) -> dict:
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


# =====================================================
# 计划过滤器
# =====================================================

def _filter_plan(plan: dict, question: str) -> dict:
    """
    后置规则过滤器：移除 SQL+ 计划中冗余的 search_knowledge 步骤。

    只过滤"混合计划"（SQL + RAG），不触碰纯知识检索计划（RAG-only）。
    RAG-only 说明 Planner 判断该问题无法用数据库回答，必须走知识库。
    """
    nodes = plan.get("nodes", {})
    edges = plan.get("edges", {})

    has_sql = any(
        n.get("capability") == "sql.query"
        for n in nodes.values()
    )

    # RAG-only 计划（无 SQL）→ 保留
    if not has_sql:
        return plan

    # 问题明确包含知识库关键词 → 保留 RAG
    if is_knowledge_question(question):
        return plan

    # 找出所有 rag.search 步骤
    rag_steps = [
        sid for sid, node in nodes.items()
        if node.get("capability") == "rag.search"
    ]

    if not rag_steps:
        return plan

    # 移除 RAG 步骤
    for sid in rag_steps:
        del nodes[sid]
        logger.info(f"[Planner] 后置过滤: 移除无关 RAG 步骤 step={sid}（问题不含知识库关键词）")

    # 清理 edges 中对已移除步骤的引用
    cleaned_edges = {}
    for target, deps in edges.items():
        if target in rag_steps:
            continue
        cleaned_deps = [d for d in deps if d not in rag_steps]
        if cleaned_deps:
            cleaned_edges[target] = cleaned_deps

    # 如果 report.generate 的依赖全部被移除，移除该 report 步骤的依赖声明
    for sid, node in nodes.items():
        if node.get("capability") == "report.generate":
            remaining_deps = cleaned_edges.get(sid, [])
            if not remaining_deps and sid in edges:
                del cleaned_edges[sid]

    return {"nodes": nodes, "edges": cleaned_edges}


def _ensure_knowledge_step(plan: dict, question: str) -> dict:
    """
    知识类问题强制补 RAG：如果问题明确涉及知识库内容，但 Planner 未生成 RAG 步骤，自动补充。
    解决小模型 Planner 将知识类问题误路由到 SQL 的问题。
    """
    if not is_knowledge_question(question):
        return plan

    nodes = plan.get("nodes", {})
    has_rag = any(
        n.get("capability") == "rag.search"
        for n in nodes.values()
    )
    if has_rag:
        return plan

    # 找最大 step_id 数字，插入新步骤
    max_id = 0
    for sid in nodes:
        try:
            max_id = max(max_id, int(sid))
        except ValueError:
            pass

    new_id = str(max_id + 1)
    nodes[new_id] = {
        "step_id": new_id,
        "capability": "rag.search",
        "description": f"检索: {question[:50]}",
        "params": {"question": question},
    }
    logger.info(f"[Planner] 知识类问题强制补 RAG step={new_id}")
    return plan


def _fallback_plan(question: str) -> dict:
    """空计划时的兜底：自动走知识库检索"""
    return {
        "nodes": {
            "1": {
                "step_id": "1",
                "capability": "rag.search",
                "description": f"检索: {question[:50]}",
                "params": {"question": question},
            }
        },
        "edges": {},
    }


def _normalize_plan(plan: dict) -> dict:
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
