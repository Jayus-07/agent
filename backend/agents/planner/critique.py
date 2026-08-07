"""
critique.py — Plan Critique 节点（P2 性能优化：规则化）

旧实现: LLM 审查 → 11s 延迟，多数情况零改动
新实现: 规则校验 0ms，仅 anomaly 时调 LLM 修正

规则引擎:
  1. capability 是否存在？→ tool_registry 查表 0ms
  2. edges 引用的 node_id 是否存在？→ 集合差集 0ms
  3. 问题含"分析/风险/建议"但缺少 business.analyze？→ 自动注入
  4. 多余 rag.search？（纯 SQL 场景）→ 自动移除
  5. 以上全通过 → 跳过 LLM，直接返回原计划
  6. 有 anomaly → 调 LLM 修正（与旧行为一致）

设计原则:
  - 信任原计划：基本合理就直接返回
  - 优雅降级：规则失败不阻塞
"""
import json

from backend.infra.llm import llm
from backend.orchestration.tool_registry import tool_registry
from backend.agents.planner.planner import _extract_json, _normalize_plan
from backend.observability.alerts import make_alert, log_degradation
from backend.prompts.critique import PLAN_CRITIQUE_SYSTEM
from backend.shared.logger import logger
from backend.config import ENABLE_PLAN_CRITIQUE

# ── 规则引擎需要知道的 Knowledge Skill ──
_KNOWLEDGE_CAPS = {"rag.search", "web.search", "web.crawl"}
_ANALYSIS_TRIGGER_WORDS = ["分析", "风险", "建议", "洞察", "报告", "report", "insight"]


def _check_capabilities_exist(nodes: dict) -> list[str]:
    """规则1: 检查所有 capability 是否已注册。返回问题列表。"""
    valid = set(tool_registry.get_available_capabilities())
    issues = []
    for sid, node in nodes.items():
        cap = node.get("capability", "")
        if cap and cap not in valid:
            issues.append(f"步骤{sid}: capability '{cap}' 未注册")
    return issues


def _check_edges_valid(nodes: dict, edges: dict) -> list[str]:
    """规则2: 检查 edges 中引用的 node_id 是否存在。"""
    node_ids = set(nodes.keys())
    issues = []
    for sid, deps in edges.items():
        if sid not in node_ids:
            issues.append(f"edges 中的步骤 '{sid}' 不在 nodes 中")
        for dep in deps:
            if dep not in node_ids:
                issues.append(f"步骤 '{sid}' 依赖 '{dep}'，但 '{dep}' 不在 nodes 中")
    return issues


def _check_missing_analysis(question: str, nodes: dict) -> list[str]:
    """规则3: 问题含分析意图但缺少 business.analyze。"""
    q_lower = question.lower()
    has_analysis_cap = any(
        n.get("capability") == "business.analyze" for n in nodes.values()
    )
    needs_analysis = any(w in q_lower for w in _ANALYSIS_TRIGGER_WORDS)
    if needs_analysis and not has_analysis_cap:
        return ["问题包含分析意图，但计划缺少 business.analyze"]
    return []


def _check_redundant_knowledge(question: str, nodes: dict) -> list[str]:
    """规则4: 纯数据结构化查询不需要 RAG 知识检索。"""
    caps = {n.get("capability", "") for n in nodes.values()}
    data_only_caps = {"sql.query", "data.export", "report.generate"}
    # 如果只有数据类 capability 且问题不含知识类关键词
    q_lower = question.lower()
    is_knowledge_question = any(
        w in q_lower for w in ["怎么", "如何", "规则", "策略", "SOP", "指南", "规定"]
    )
    if caps.issubset(data_only_caps | {"sql.query", "business.analyze", "report.generate"}):
        redundant = caps & _KNOWLEDGE_CAPS
        if redundant and not is_knowledge_question:
            return [f"纯数据查询场景，以下知识检索步骤多余: {redundant}"]
    return []


def _run_rules(question: str, plan: dict) -> list[str]:
    """跑所有规则，返回问题列表。空列表表示计划无需修正。"""
    nodes = plan.get("nodes", {})
    edges = plan.get("edges", {})
    if not nodes:
        return ["计划为空"]

    issues = []
    issues += _check_capabilities_exist(nodes)
    issues += _check_edges_valid(nodes, edges)
    issues += _check_missing_analysis(question, nodes)
    issues += _check_redundant_knowledge(question, nodes)
    return issues


def _auto_fix_plan(plan: dict, issues: list[str], question: str) -> dict:
    """规则引擎自动修复部分问题，剩下的复杂问题交给 LLM。"""
    nodes = dict(plan.get("nodes", {}))
    edges = dict(plan.get("edges", {}))
    modified = False

    for issue in issues:
        # 自动注入 business.analyze
        if "缺少 business.analyze" in issue:
            # 找到 sql.query 的 steps
            sql_steps = sorted([
                sid for sid, n in nodes.items()
                if n.get("capability") == "sql.query"
            ])
            if sql_steps:
                new_id = str(max(int(s) for s in nodes.keys()) + 1)
                nodes[new_id] = {
                    "step_id": new_id,
                    "capability": "business.analyze",
                    "description": "分析数据中的业务风险和机会",
                    "params": {},
                }
                # 依赖所有 sql.query 步骤
                edges[new_id] = sql_steps
                # 如果存在 report.generate，让它依赖 business.analyze
                for sid, n in nodes.items():
                    if n.get("capability") == "report.generate":
                        edges[sid] = [new_id]
                modified = True
                logger.info("[Critique:auto] 自动注入 business.analyze 步骤")

        # 自动移除多余 knowledge 步骤
        if "知识检索步骤多余" in issue:
            for sid in list(nodes.keys()):
                if nodes[sid].get("capability") in _KNOWLEDGE_CAPS:
                    del nodes[sid]
                    # 清理 edges
                    edges = {
                        k: [d for d in v if d != sid]
                        for k, v in edges.items() if k != sid
                    }
                    # 移除孤立 edges
                    edges = {k: v for k, v in edges.items() if v}
                    modified = True
                    logger.info(f"[Critique:auto] 移除多余知识检索步骤 {sid}")

    if not modified:
        return plan

    return {"nodes": nodes, "edges": edges}


def critique_node(state: dict) -> dict:
    """Plan Critique 节点：规则校验（0ms）+ LLM 修正（仅 anomaly 时）。

    触发条件:
      - ENABLE_PLAN_CRITIQUE = True
      - 计划步骤 > 1
    """
    plan = state.get("plan", {"nodes": {}, "edges": {}})
    question = state.get("question", "")

    if not ENABLE_PLAN_CRITIQUE:
        return {"plan": plan, "_plan_critiqued": False, "_plan_changed": False}

    node_count = len(plan.get("nodes", {}))
    if node_count <= 1:
        logger.info(f"[Critique] 单步骤计划 ({node_count} 步骤)，跳过审查")
        return {"plan": plan, "_plan_critiqued": False, "_plan_changed": False}

    logger.info(f"[Critique] 开始规则审查 ({node_count} 步骤)")

    # ── 阶段1: 规则引擎（0ms）──
    issues = _run_rules(question, plan)

    if not issues:
        logger.info("[Critique] 规则校验通过，跳过 LLM 审查")
        return {"plan": plan, "_plan_critiqued": True, "_plan_changed": False}

    logger.info(f"[Critique] 发现 {len(issues)} 个问题: {issues}")

    # ── 阶段2: 自动修复 ──
    auto_fixed = _auto_fix_plan(plan, issues, question)

    # 判断剩余问题是否需要 LLM
    remaining = _run_rules(question, auto_fixed)
    if not remaining:
        logger.info("[Critique] 规则引擎自动修复完成，跳过 LLM")
        plan_changed = json.dumps(plan, sort_keys=True) != json.dumps(auto_fixed, sort_keys=True)
        return {"plan": auto_fixed, "_plan_critiqued": True, "_plan_changed": plan_changed}

    # ── 阶段3: LLM 修正（仅余复杂问题）──
    logger.info(f"[Critique] {len(remaining)} 个问题需 LLM 修正: {remaining}")
    capabilities_schema = tool_registry.get_capabilities_schema_text()
    system_prompt = PLAN_CRITIQUE_SYSTEM.format(capabilities_schema=capabilities_schema)

    user_message = f"""原始用户问题: {question}

当前计划:
{json.dumps(auto_fixed, ensure_ascii=False, indent=2)}

规则引擎发现以下问题（请逐一修正）:
{chr(10).join(f'- {i}' for i in remaining)}

如果计划无需修改，返回原 JSON。"""

    try:
        response = llm.invoke([
            ("system", system_prompt),
            ("human", user_message),
        ])
        content = response.content if hasattr(response, "content") else str(response)
        corrected = _extract_json(content)

        if not corrected or not corrected.get("nodes"):
            logger.warning("[Critique] LLM 返回空计划，使用规则修复结果")
            plan_changed = json.dumps(plan, sort_keys=True) != json.dumps(auto_fixed, sort_keys=True)
            return {"plan": auto_fixed, "_plan_critiqued": True, "_plan_changed": plan_changed}

        corrected = _normalize_plan(corrected)
        plan_changed = (
            json.dumps(plan, sort_keys=True) != json.dumps(corrected, sort_keys=True)
        )

        if plan_changed:
            logger.info(
                f"[Critique] LLM 已修正: "
                f"原={len(plan.get('nodes',{}))}步 → 新={len(corrected.get('nodes',{}))}步"
            )
        else:
            logger.info("[Critique] LLM 确认无需修改")

        return {"plan": corrected, "_plan_critiqued": True, "_plan_changed": plan_changed}

    except Exception as e:
        logger.warning(f"[Critique] LLM 审查失败，使用规则修复结果: {e}")
        plan_changed = json.dumps(plan, sort_keys=True) != json.dumps(auto_fixed, sort_keys=True)
        return {"plan": auto_fixed, "_plan_critiqued": True, "_plan_changed": plan_changed}
