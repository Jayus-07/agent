"""
planner.py — Planner 节点

职责:
  - 理解用户问题
  - 拆解任务为子步骤
  - 生成 DAG 执行计划 (nodes + edges)
  - 不调用任何工具

Planner 只输出 capability，不指定具体 tool。
Tool 选择由 Supervisor + ToolRegistry 完成。
"""

import json

from backend.infra.llm import llm
from backend.orchestration.tool_registry import tool_registry
from backend.observability.alerts import make_alert, log_degradation
from backend.prompts.planner import PLANNER_SYSTEM, is_knowledge_question
from backend.shared.logger import logger


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
    """
    question = state.get("question", "")
    kb_id = state.get("kb_id", "default")

    if not question:
        logger.warning("[Planner] 空问题，返回空 plan")
        return {"plan": {"nodes": {}, "edges": {}}}

    capabilities_schema = _format_capabilities_schema()
    cap_example = tool_registry.get_available_capabilities()[0]

    prompt = PLANNER_SYSTEM.format(
        capabilities_schema=capabilities_schema,
        cap_example=cap_example,
    )
    user_msg = f"用户问题: {question}\n\n请输出 JSON:"

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
# JSON 修复管道（4 层）
# =====================================================

def _strip_markdown_code_block(text: str) -> str:
    """去除 markdown 代码块标记 (```json ... ```)"""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def _find_outer_braces(text: str) -> tuple[int, int] | None:
    """找到最外层的 { } 边界，返回 (start, end) 或 None"""
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return start, end
    return None


def _replace_single_quotes_in_json(text: str) -> str:
    """在 JSON 上下文中的单引号替换为双引号（保守策略：仅替换 key 和顶层字符串值）"""
    import re
    text = re.sub(r"'([^']*)'(\s*:)", r'"\1"\2', text)
    text = re.sub(r"(:\s*)'([^']*)'", r'\1"\2"', text)
    return text


def _fix_unquoted_keys(text: str) -> str:
    """修复缺失引号的 key: {key: "value"} -> {"key": "value"}"""
    import re
    text = re.sub(r'([{,])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', text)
    return text


def _repair_common_json_errors(text: str) -> str:
    """修复小模型常见的 JSON 格式错误"""
    import re
    # 1. 尾逗号
    text = re.sub(r',\s*}', '}', text)
    text = re.sub(r',\s*]', ']', text)
    # 2. 中文引号 → 英文引号
    text = text.replace('“', '"').replace('”', '"')
    text = text.replace('‘', "'").replace('’', "'")
    # 3. 未转义的控制字符在字符串值中
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    # 4. 缺失引号的 key
    text = _fix_unquoted_keys(text)
    # 5. 单引号替换
    text = _replace_single_quotes_in_json(text)
    return text


def _brute_force_extract(text: str) -> dict:
    """暴力提取：用正则找最外层的完整 JSON 对象"""
    import re
    matches = re.findall(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
    for match in sorted(matches, key=len, reverse=True):
        try:
            return json.loads(match)
        except json.JSONDecodeError:
            continue
    return {}


def _extract_json(text: str) -> dict:
    """4 层修复管道：每层尝试解析，成功即返回。

    Layer 0: 直接解析（最快路径）
    Layer 1: 截取最外层 {} 再解析
    Layer 2: 修复常见小模型 JSON 错误后解析
    Layer 3: 暴力正则提取

    全失败返回空 dict（触发 _fallback_plan）。
    """
    text = _strip_markdown_code_block(text)

    # Layer 0: 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Layer 1: 截取最外层 {}
    bounds = _find_outer_braces(text)
    if bounds:
        try:
            return json.loads(text[bounds[0]:bounds[1] + 1])
        except json.JSONDecodeError:
            pass

    # Layer 2: 修复常见错误
    try:
        repaired = _repair_common_json_errors(text)
        bounds = _find_outer_braces(repaired)
        if bounds:
            return json.loads(repaired[bounds[0]:bounds[1] + 1])
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # Layer 3: 暴力提取
    result = _brute_force_extract(text)
    if result:
        return result

    # 全失败
    logger.warning("[Planner] JSON 修复管道全部失败，触发兜底")
    alert = make_alert("PLAN_JSON_INVALID", {"text_preview": text[:200]})
    log_degradation(alert)
    return {}


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
