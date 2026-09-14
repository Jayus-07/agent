"""travel/experts/budget.py — 预算专家

职责：算清花费并拆分口径。**不判定是否超预算** —— 那是 validator 的
轴四（这样"超支"的判定口径只有一处，改阈值不会两处不同步）。

只做一件判定之外的事：当用户没给预算时，把「本次没做预算校验」如实
写进提示，避免用户误以为行程已经过预算把关。
"""
from __future__ import annotations

from backend.shared.logger import logger
from backend.tools.travel.cost import estimate_cost
from backend.travel.experts.base import run_expert_safely
from backend.travel.graph_state import load_brief, load_itinerary, save_itinerary


def budget_expert_node(state: dict) -> dict:
    """预算专家节点：填充 CostBreakdown。"""
    def _run(_state: dict) -> dict:
        brief = load_brief(state)
        itinerary = load_itinerary(state)
        if itinerary is None:
            return {"status": "failed", "data": {}, "notes": [],
                    "error": "行程尚未生成，无法核算预算"}

        itinerary.cost = estimate_cost(itinerary.days, brief.party_size)

        notes: list[str] = []
        if brief.budget_cny is None or brief.budget_cny <= 0:
            notes.append(
                "你未提供预算，本次未做预算校验；"
                f"以下为按常见消费水平估算的总额 ¥{itinerary.cost.total:.0f}"
            )

        logger.info("[TravelBudget] total=%.0f breakdown=%s",
                    itinerary.cost.total, itinerary.cost.model_dump())
        return {"status": "success",
                "data": {"itinerary": save_itinerary(itinerary),
                         "cost": itinerary.cost.model_dump()},
                "notes": notes}

    result = run_expert_safely("budget", _run, state)
    data = result.get("data") or {}

    history = list(state.get("expert_history", []))
    history.append({"expert": "budget", "status": result.get("status", "failed"),
                    "duration_ms": result.get("duration_ms", 0)})

    update: dict = {
        "last_expert_result": dict(result),
        "expert_history": history,
        "notes": list(state.get("notes", [])) + list(result.get("notes", [])),
    }
    if data.get("itinerary"):
        update["itinerary"] = data["itinerary"]
    return update
