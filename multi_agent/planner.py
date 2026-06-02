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
from typing import List

from llm.llm_factory import llm
from multi_agent.tool_registry import tool_registry
from utils.logger import logger

# =====================================================
# Planner Prompt
# =====================================================

PLANNER_SYSTEM = """你是任务规划专家。分析用户问题，将其拆解为可并行或串行的子任务。

## 可用能力（及参数格式 — 必须严格使用）

{capabilities_schema}

## 输出格式（严格的 JSON，不要解释）
{{
    "nodes": [
        {{"step_id": "1",
          "capability": "{cap_example}",
          "description": "步骤描述",
          "params": {{"question": "具体的查询问题"}}
        }}
    ],
    "edges": {{}}
}}

## 完整示例

示例 1 — 简单查询：
  用户: "技术部有多少人"
  → nodes: [{{"step_id": "1", "capability": "query_database",
              "description": "查询技术部人数",
              "params": {{"question": "查询技术部有多少人"}}}}]
  → edges: {{}}

示例 2 — 并行查询无依赖：
  用户: "查询项目预算情况，同时从知识库查找项目管理经验"
  → nodes: [
      {{"step_id": "1", "capability": "query_database",
        "description": "查询项目预算", "params": {{"question": "查询项目预算情况"}}}},
      {{"step_id": "2", "capability": "search_knowledge",
        "description": "检索项目管理经验", "params": {{"question": "项目管理最佳实践"}}}}
    ]
  → edges: {{}}

示例 3 — DAG（SQL+RAG 并行 → 最终报告）：
  用户: "分析技术部预算使用情况，并从知识库查找项目经验，最后生成一份部门综合分析报告"
  → nodes: [
      {{"step_id": "1", "capability": "query_database",
        "description": "查询技术部预算数据",
        "params": {{"question": "查询技术部所有项目的预算总额和使用情况"}}}},
      {{"step_id": "2", "capability": "search_knowledge",
        "description": "检索类似项目经验",
        "params": {{"question": "技术部门预算管理和项目经验"}}}},
      {{"step_id": "3", "capability": "generate_report",
        "description": "生成部门综合分析报告",
        "params": {{"report_type": "dept_summary", "filters": {{"dept": "技术部"}}}}}}
    ]
  → edges: {{"3": ["1", "2"]}}

## edges 含义（重要！）
edges 的 key 是"需要等待的步骤"，
value 是"必须先完成的步骤列表"。
- step 3 需要 step 1 和 step 2 的数据 → edges: {{"3": ["1", "2"]}}
- step 2 需要 step 1 的结果 → edges: {{"2": ["1"]}}
- 无依赖的步骤不出现在 edges 中，它们会自动并行执行

## 规则
1. capability 必须从上方列表中选择，params 的 key 必须与上方能力定义的参数名完全一致
2. params 值用自然语言描述，包含足够上下文
3. 无依赖的步骤不出现在 edges 中，可并行的步骤不设依赖
4. 只有真正的数据依赖才添加 edges（A 的输出是 B 的输入）
5. 简单问题只需 1 个 node，edges 为 {{}}
6. **当用户问题包含"报告"/"分析报告"/"生成报告"/"汇总"/"总结"等词时，必须在最后一步添加 generate_report**
7. 如果用户问题无法拆解为任何已知能力，返回 {{"nodes": [], "edges": {{}}}}
8. 只输出 JSON，不要加 markdown 代码块或任何解释"""


# =====================================================
# Planner 节点
# =====================================================

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

        # 提取 JSON
        content = _extract_json(content)
        plan = json.loads(content)

        # 校验
        plan = _normalize_plan(plan)

        node_count = len(plan.get("nodes", {}))
        edge_count = len(plan.get("edges", {}))
        logger.info(f"[Planner] 计划生成: {node_count} 个节点, {edge_count} 条依赖")

        return {"plan": plan}

    except json.JSONDecodeError as e:
        logger.error(f"[Planner] JSON 解析失败: {e}, 内容: {content[:200]}")
        return {"plan": {"nodes": {}, "edges": {}}}
    except Exception as e:
        logger.error(f"[Planner] 规划失败: {e}")
        return {"plan": {"nodes": {}, "edges": {}}}


# =====================================================
# 辅助函数
# =====================================================

def _extract_json(text: str) -> str:
    """从 LLM 输出中提取 JSON 部分"""
    # 去掉可能的 markdown 代码块标记
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    # 找到 JSON 边界
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        return text[start:end + 1]
    return text


def _format_capabilities_schema() -> str:
    """格式化所有 capability 的 schema 为 Planner prompt 用"""
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


def _normalize_plan(plan: dict) -> dict:
    """校验并规范化 plan 结构"""
    valid_capabilities = set(tool_registry.get_available_capabilities())

    # 确保 nodes 是 dict 格式
    raw_nodes = plan.get("nodes", [])
    nodes = {}

    if isinstance(raw_nodes, list):
        for node in raw_nodes:
            sid = str(node.get("step_id", ""))
            capability = node.get("capability", "")

            # 校验 capability
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
            # 确保 deps 是 list
            if isinstance(deps, str):
                deps = [deps]
            elif not isinstance(deps, list):
                deps = []
            deps = [str(d) for d in deps]
            edges[str(key)] = deps

    return {"nodes": nodes, "edges": edges}
