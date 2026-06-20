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

## 数据库包含的数据

数据库有 4 张表：departments(部门)、users(用户/员工)、projects(项目)、project_members(项目成员)。
数据涵盖：部门名称、员工姓名/邮箱/角色、项目名称/预算/状态/日期、项目参与者。
**不包括**：销售数据、订单、商品、库存、交易记录。

## 可用能力（及参数格式 — 必须严格使用）

{capabilities_schema}

## 能力选择指南（非常重要！）

**query_database（查询数据库）**— 用于：
  - 统计/排行/筛选/聚合/对比 等数据类问题
  - 任何涉及项目预算、员工人数、部门信息的查询
  - 典型：统计人数、查询项目预算、列出部门员工、筛选某状态项目
  - **纯数据查询不要同时加 search_knowledge**

**search_knowledge（知识库检索）**— **仅在**以下情况使用：
  - 问题明确包含：制度、流程、规范、经验、文档、操作手册、最佳实践、方案参考
  - 概念解释、定义类问题（"什么是..."、"XX 是什么"）
  - 判断/可行性问题（"能不能/可以吗/是否应该"）
  - 技术知识、操作标准
  - **反面例子：下面这些情况不需要 search_knowledge**：
    * "分析技术部预算使用情况" → 纯数据查询，SQL 即可
    * "统计各部门人数" → 纯统计，SQL 即可
    * "列出所有项目" → 纯查询，SQL 即可
    * "对比各项目预算" → 纯对比，SQL 即可

**generate_report（生成报告）**— 仅在前面有数据查询或检索步骤时使用

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

示例 1 — 纯数据库查询：
  用户: "技术部有多少人"
  → nodes: [{{"step_id": "1", "capability": "query_database",
              "description": "查询技术部人数",
              "params": {{"question": "查询技术部有多少人"}}}}]
  → edges: {{}}

示例 2 — 纯知识检索：
  用户: "叶菜类能不能第二天卖"
  → nodes: [{{"step_id": "1", "capability": "search_knowledge",
              "description": "查询叶菜类保鲜和销售规则",
              "params": {{"question": "叶菜类蔬菜的保鲜期和第二天是否可以售卖"}}}}]
  → edges: {{}}

示例 3 — 数据分析 + 报告（不需要 RAG）：
  用户: "分析技术部预算使用情况，并生成报告"
  → nodes: [
      {{"step_id": "1", "capability": "query_database",
        "description": "查询技术部预算数据",
        "params": {{"question": "查询技术部所有项目的预算总额和使用情况"}}}},
      {{"step_id": "2", "capability": "generate_report",
        "description": "生成预算分析报告",
        "params": {{"report_type": "dept_summary", "filters": {{"dept": "技术部"}}}}}}
    ]
  → edges: {{"2": ["1"]}}
  **注意：这是纯数据分析问题，不需要 search_knowledge。**

示例 4 — 数据库 + RAG 并行（只有问题明确需要经验/规范时才用）：
  用户: "查询项目预算，同时查找项目管理规范和经验"
  → nodes: [
      {{"step_id": "1", "capability": "query_database",
        "description": "查询项目预算", "params": {{"question": "查询项目预算情况"}}}},
      {{"step_id": "2", "capability": "search_knowledge",
        "description": "检索项目管理规范", "params": {{"question": "项目管理规范和经验"}}}}
    ]
  → edges: {{}}

示例 5 — 完整 DAG（SQL+RAG+报告）：
  用户: "分析技术部预算使用情况，查找制度规范，生成综合分析报告"
  → nodes: [
      {{"step_id": "1", "capability": "query_database",
        "description": "查询技术部预算数据",
        "params": {{"question": "查询技术部所有项目的预算总额和使用情况"}}}},
      {{"step_id": "2", "capability": "search_knowledge",
        "description": "检索预算管理制度",
        "params": {{"question": "预算管理制度规范"}}}},
      {{"step_id": "3", "capability": "generate_report",
        "description": "生成综合分析报告",
        "params": {{"report_type": "dept_summary", "filters": {{"dept": "技术部"}}}}}}
    ]
  → edges: {{"3": ["1", "2"]}}

## edges 含义（重要！）
edges 的 key 是"需要等待的步骤"，value 是"必须先完成的步骤列表"。
- step 3 需要 step 1 和 step 2 的数据 → edges: {{"3": ["1", "2"]}}
- 无依赖的步骤不出现在 edges 中，它们会自动并行执行

## 规则
1. capability 必须从上方列表中选择
2. **优先判断问题是否涉及数据库已有的 4 张表，不涉及则用 search_knowledge**
3. **纯数据分析/统计/排行/预算类问题 → 只用 query_database + generate_report，不要添加 search_knowledge**
4. **仅当问题明确提到"制度/规范/流程/经验/文档/最佳实践"等词时才添加 search_knowledge**
5. 无依赖的步骤可并行，只有数据依赖才添加 edges
6. **用户问题含"报告/分析报告/汇总/总结"时，最后一步添加 generate_report**
7. 无法匹配任何能力时返回 {{"nodes": [], "edges": {{}}}}
8. 只输出 JSON，不要解释"""


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

        # 提取 JSON
        content = _extract_json(content)
        plan = json.loads(content)

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
            if node.get("capability") == "search_knowledge":
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


# 知识库关键词：问题包含这些词时才可能需要 search_knowledge
_KNOWLEDGE_KEYWORDS = [
    # 制度/规范/操作类
    "制度", "规范", "流程", "经验", "文档", "最佳实践",
    "操作手册", "指南", "标准", "方案", "方法",
    "手册", "规定", "要求", "规则", "条例",
    # 概念/定义类
    "是什么", "什么是", "定义", "概念", "介绍", "原理",
    # 可行性/判断类
    "能不能", "可以吗", "是否应该", "是否允许", "是否可行",
    "是否", "能不能", "行不行", "可以不可以",
    "可以吗", "能吗", "会吗", "还能",
    # 操作/How-to 类
    "应该怎么", "怎么配置", "如何使用", "如何部署",
    "怎样", "如何", "怎么做", "咋做", "咋处理",
    # 时效/存储/条件类（经常问保鲜期、保质期、存放条件）
    "保鲜", "保质", "存储", "储存", "存放", "保存",
    "多久", "多长时间", "什么条件", "什么情况",
    "报废", "报损", "下架", "上架", "售卖", "翻包",
]

def is_knowledge_question(question: str) -> bool:
    """判断问题是否需要知识库检索"""
    return any(kw in question for kw in _KNOWLEDGE_KEYWORDS)


def _filter_plan(plan: dict, question: str) -> dict:
    """
    后置规则过滤器：移除 SQL+ 计划中冗余的 search_knowledge 步骤。

    只过滤"混合计划"（SQL + RAG），不触碰纯知识检索计划（RAG-only）。
    RAG-only 说明 Planner 判断该问题无法用数据库回答，必须走知识库。
    """
    nodes = plan.get("nodes", {})
    edges = plan.get("edges", {})

    # 计划中是否有 SQL 步骤
    has_sql = any(
        n.get("capability") == "query_database"
        for n in nodes.values()
    )

    # RAG-only 计划（无 SQL）→ 保留，这是纯知识库问题
    if not has_sql:
        return plan

    # 问题明确包含知识库关键词 → 保留 RAG
    if is_knowledge_question(question):
        return plan

    # 找出所有 search_knowledge 步骤
    rag_steps = [
        sid for sid, node in nodes.items()
        if node.get("capability") == "search_knowledge"
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

    # 如果 generate_report 的依赖全部被移除，移除该 report 步骤的依赖声明
    for sid, node in nodes.items():
        if node.get("capability") == "generate_report":
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
        n.get("capability") == "search_knowledge"
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
        "capability": "search_knowledge",
        "description": f"检索: {question[:50]}",
        "params": {"question": question},
    }
    logger.info(f"[Planner] 知识类问题强制补 RAG step={new_id}")
    return plan


def _fallback_plan(question: str) -> dict:
    """空计划时的兜底：自动走知识库检索（kb_id 由调用方在 planner_node 中注入）"""
    return {
        "nodes": {
            "1": {
                "step_id": "1",
                "capability": "search_knowledge",
                "description": f"检索: {question[:50]}",
                "params": {"question": question},
            }
        },
        "edges": {},
    }


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
