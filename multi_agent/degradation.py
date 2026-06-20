"""
degradation.py — 通用降级链

能力降级注册表 + 降级执行逻辑。
每个步骤最多降级 1 次，防止无限降级循环。

降级触发场景:
  - SQL 返回空结果 → RAG 知识库检索
  - RAG 无匹配结果 → SQL 数据库查询
  - Report 缺少数据 → RAG 知识库检索
"""

from multi_agent.tool_registry import tool_registry
from multi_agent.alerts import make_alert, log_degradation
from utils.logger import logger

# =====================================================
# 降级链配置
# =====================================================

MAX_DEGRADATION_PER_STEP = 1

DEGRADATION_CHAIN: dict[str, list[str]] = {
    "query_database":   ["search_knowledge"],      # SQL 空 → 知识库
    "search_knowledge": ["query_database"],         # 知识库无结果 → SQL
    "generate_report":  ["search_knowledge"],       # 报告缺数据 → 知识库
}


# =====================================================
# 降级逻辑
# =====================================================

def can_degrade(step_id: str, attempted: set[str]) -> bool:
    """检查该步骤是否还可以降级（防止无限降级循环）"""
    # 如果该步骤本身已经在已降级集合中，不允许继续降级
    if step_id in attempted:
        return False
    # 检查该步骤已经产生了多少个降级衍生步骤
    degradation_count = sum(
        1 for aid in attempted if aid.startswith(step_id + "_") or aid == step_id + "_rag_fallback"
    )
    return degradation_count < MAX_DEGRADATION_PER_STEP


def get_fallback_capability(capability: str) -> str | None:
    """获取某个能力的降级目标 capability"""
    chain = DEGRADATION_CHAIN.get(capability, [])
    return chain[0] if chain else None


def execute_degradation(
    nodes: dict,
    edges: dict,
    step_results: dict,
    ready_dispatch: list,
    degraded_steps: set[str],
    question: str,
) -> tuple[dict, list, set[str]]:
    """
    检查所有已完成的步骤，对需要降级的执行降级。

    参数:
        nodes:          当前的 plan.nodes（可修改）
        edges:          当前的 plan.edges
        step_results:   当前步骤结果集
        ready_dispatch: 已有的就绪派发列表（会追加）
        degraded_steps: 已经降级过的步骤集合（防止重复）
        question:       原始用户问题

    返回:
        (step_results, ready_dispatch, degraded_steps) — 可能已修改
    """
    has_plan_changed = False

    for sid, node in list(nodes.items()):
        sr = step_results.get(sid, {})
        capability = node.get("capability", "")

        # 跳过非 success 步骤
        if sr.get("status") != "success":
            continue

        # 检查是否需要降级：is_empty 或 row_count == 0
        is_empty = sr.get("is_empty", False)
        row_count = sr.get("row_count")
        if not is_empty and row_count != 0:
            continue

        # 检查降级条件
        if not can_degrade(sid, degraded_steps):
            continue

        fallback_cap = get_fallback_capability(capability)
        if not fallback_cap:
            continue

        # 检查 plan 中是否已有同类型步骤
        has_same_cap = any(
            n.get("capability") == fallback_cap
            for n in nodes.values()
        )
        if has_same_cap:
            logger.info(f"[Degradation] step={sid} 降级目标 {fallback_cap} 已在计划中，跳过")
            continue

        # 执行降级：插入新节点
        fallback_id = f"{sid}_fallback"
        original_question = str(node.get("params", {}).get("question", question))
        nodes[fallback_id] = {
            "step_id": fallback_id,
            "capability": fallback_cap,
            "description": f"降级检索 ({capability} 无结果): {original_question[:30]}...",
            "params": {"question": original_question},
        }

        worker = tool_registry.get_worker(fallback_cap)
        if worker:
            degraded_steps.add(fallback_id)
            step_results[fallback_id] = {"status": "pending"}
            ready_dispatch.append({"worker": worker, "step_id": fallback_id})
            has_plan_changed = True

            alert = make_alert("DEGRADATION_TRIGGER", {
                "from_step": sid,
                "from_capability": capability,
                "to_step": fallback_id,
                "to_capability": fallback_cap,
            })
            log_degradation(alert)
            logger.info(
                f"[Degradation] {capability} → {fallback_cap} "
                f"(step {sid} → {fallback_id})"
            )

    return step_results, ready_dispatch, degraded_steps
