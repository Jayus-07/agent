"""
degradation.py — Capability 降级链

每个步骤最多降级 1 次，防止无限降级循环。

降级触发场景:
  - sql.query 返回空 → 降级到 rag.search
  - rag.search 无结果 → 降级到 sql.query
  - report.generate 缺数据 → 降级到 rag.search
"""

from backend.orchestration.tool_registry import tool_registry
from backend.orchestration.supervisor.alerts import make_alert, log_degradation
from backend.shared.logger import logger


MAX_DEGRADATION_PER_STEP = 1

DEGRADATION_CHAIN: dict[str, list[str]] = {
    "sql.query":        ["rag.search"],       # SQL 空 → 知识库
    "rag.search":       ["sql.query"],        # 知识库无结果 → SQL
    "report.generate":  ["rag.search"],       # 报告缺数据 → 知识库
}


def can_degrade(step_id: str, attempted: set[str]) -> bool:
    if step_id in attempted:
        return False
    degradation_count = sum(
        1 for aid in attempted if aid.startswith(step_id + "_") or aid == step_id + "_rag_fallback"
    )
    return degradation_count < MAX_DEGRADATION_PER_STEP


def get_fallback_capability(capability: str) -> str | None:
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
    """检查所有已完成的步骤，对需要降级的执行降级。"""
    for sid, node in list(nodes.items()):
        sr = step_results.get(sid, {})
        capability = node.get("capability", "")

        if sr.get("status") != "success":
            continue

        is_empty = sr.get("is_empty", False)
        row_count = sr.get("row_count")
        if not is_empty and row_count != 0:
            continue

        if not can_degrade(sid, degraded_steps):
            continue

        fallback_cap = get_fallback_capability(capability)
        if not fallback_cap:
            continue

        has_same_cap = any(
            n.get("capability") == fallback_cap
            for n in nodes.values()
        )
        if has_same_cap:
            logger.info(f"[Degradation] step={sid} 降级目标 {fallback_cap} 已在计划中，跳过")
            continue

        fallback_id = f"{sid}_fallback"
        original_question = str(node.get("params", {}).get("question", question))
        nodes[fallback_id] = {
            "step_id": fallback_id,
            "capability": fallback_cap,
            "description": f"降级检索 ({capability} 无结果): {original_question[:30]}...",
            "params": {"question": original_question},
        }

        worker = tool_registry.get_node(fallback_cap)
        if worker:
            degraded_steps = degraded_steps | {fallback_id}
            step_results[fallback_id] = {"status": "pending"}
            ready_dispatch.append({"worker": worker, "step_id": fallback_id})

            alert = make_alert("DEGRADATION_TRIGGER", {
                "from_step": sid, "from_capability": capability,
                "to_step": fallback_id, "to_capability": fallback_cap,
            })
            log_degradation(alert)
            logger.info(f"[Degradation] {capability} → {fallback_cap} (step {sid} → {fallback_id})")

    return step_results, ready_dispatch, degraded_steps
