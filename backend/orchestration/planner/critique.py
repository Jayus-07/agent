"""
critique.py — Plan Critique 节点

在 Planner 产出计划后、Supervisor 执行前，让 LLM 以审查员视角
审视计划，发现并修正路由/依赖错误。

设计原则:
  - 最小修改：只修正明显有问题的部分
  - 信任原计划：基本合理就直接返回
  - 优雅降级：LLM 调用失败时使用原计划，不阻塞
"""

import json

from backend.infra.llm import llm
from backend.orchestration.tool_registry import tool_registry
from backend.orchestration.planner.planner import _extract_json, _normalize_plan
from backend.observability.alerts import make_alert, log_degradation
from backend.prompts.critique import PLAN_CRITIQUE_SYSTEM
from backend.shared.logger import logger
from backend.config import ENABLE_PLAN_CRITIQUE


def critique_node(state: dict) -> dict:
    """
    Plan Critique 节点：审查并修正 Planner 产出的计划。

    触发条件:
      - ENABLE_PLAN_CRITIQUE = True
      - 计划步骤 > 1

    失败策略:
      - LLM 调用失败 → 返回原计划，不阻塞
    """
    plan = state.get("plan", {"nodes": {}, "edges": {}})
    question = state.get("question", "")

    if not ENABLE_PLAN_CRITIQUE:
        logger.info("[Critique] 已禁用（ENABLE_PLAN_CRITIQUE=False），跳过")
        return {"plan": plan, "_plan_critiqued": False, "_plan_changed": False}

    node_count = len(plan.get("nodes", {}))
    if node_count <= 1:
        logger.info(f"[Critique] 单步骤计划 ({node_count} 步骤)，跳过审查")
        return {"plan": plan, "_plan_critiqued": False, "_plan_changed": False}

    logger.info(f"[Critique] 开始审查计划 ({node_count} 步骤)")

    capabilities_schema = tool_registry.get_capabilities_schema_text()
    system_prompt = PLAN_CRITIQUE_SYSTEM.format(capabilities_schema=capabilities_schema)

    user_message = f"""原始用户问题: {question}

待审查的计划:
{json.dumps(plan, ensure_ascii=False, indent=2)}

请审查上述计划，指出并修正问题。如果计划无需修改，返回原 JSON。"""

    try:
        response = llm.invoke([
            ("system", system_prompt),
            ("human", user_message),
        ])
        content = response.content if hasattr(response, "content") else str(response)

        corrected = _extract_json(content)

        if not corrected or not corrected.get("nodes"):
            logger.warning("[Critique] LLM 返回了空计划，使用原计划")
            alert = make_alert("CRITIQUE_FAILED", {
                "reason": "empty_response", "question": question[:80],
            })
            log_degradation(alert)
            return {"plan": plan, "_plan_critiqued": False, "_plan_changed": False}

        corrected = _normalize_plan(corrected)

        original_json = json.dumps(plan, ensure_ascii=False, sort_keys=True)
        corrected_json = json.dumps(corrected, ensure_ascii=False, sort_keys=True)
        plan_changed = original_json != corrected_json

        if plan_changed:
            logger.info(
                f"[Critique] 计划已修正: "
                f"原={len(plan.get('nodes',{}))}步 → 新={len(corrected.get('nodes',{}))}步"
            )
            alert = make_alert("PLAN_MISROUTE", {
                "question": question[:80],
                "original_nodes": len(plan.get("nodes", {})),
                "corrected_nodes": len(corrected.get("nodes", {})),
            })
            log_degradation(alert)
        else:
            logger.info("[Critique] 计划无需修正")

        return {"plan": corrected, "_plan_critiqued": True, "_plan_changed": plan_changed}

    except Exception as e:
        logger.warning(f"[Critique] 审查失败，使用原计划: {e}")
        alert = make_alert("CRITIQUE_FAILED", {
            "reason": str(e)[:200], "question": question[:80],
        })
        log_degradation(alert)
        return {"plan": plan, "_plan_critiqued": False, "_plan_changed": False}
