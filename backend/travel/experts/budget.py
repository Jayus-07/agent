"""travel/experts/budget.py — 预算专家节点（Phase 3 改造：节点编排 + 兼容入口）

费用核算的调用点收敛至 services/budget_service.py（estimate_cost，城市档位）。
本文件保留 LangGraph 节点 `budget_expert_node`（state 读写 / run_expert_safely /
遥测——Node 边界冻结）。**只算不判**：超支判定是 validator 的轴四（口径
只有一处的既有纪律）；无预算时如实披露「未做预算校验」。
"""
from __future__ import annotations

from backend.shared.logger import logger
from backend.travel.core.events import run_travel_tool
from backend.travel.experts.base import run_expert_safely
from backend.travel.graph_state import load_brief, load_itinerary, save_itinerary

# 兼容面 + 节点调用面（本模块命名空间 = 补丁缝）：委托 OptimizationAgent
from backend.travel.agents.optimization_agent import OptimizationAgent

_optimization = OptimizationAgent()


def estimate_cost(days, party_size, city=""):
    """费用核算（Optimization 能力）：委托 OptimizationAgent。"""
    return _optimization.estimate_cost(days, party_size, city=city)


def budget_expert_node(state: dict) -> dict:
    """预算专家节点：填充 CostBreakdown。"""
    def _run(_state: dict) -> dict:
        brief = load_brief(state)
        itinerary = load_itinerary(state)
        if itinerary is None:
            return {"status": "failed", "data": {}, "notes": [],
                    "error": "行程尚未生成，无法核算预算"}

        # 城市档位（P0-3）：餐饮/住宿按目的地消费水平核算，未登记城市回落全局定额
        itinerary.cost = run_travel_tool(
            "travel.calculate_budget",
            "optimization",
            lambda: estimate_cost(
                itinerary.days, brief.party_size, city=brief.destination),
            result_summary=lambda value: {
                "total_cny": round(float(value.total), 2),
                # M2 验收反馈：费用四项拆解（与行程单 cost 同源）
                "category": "budget",
                "preview": [
                    {"item": "门票", "cny": round(float(value.tickets), 2)},
                    {"item": "餐饮", "cny": round(float(value.meals), 2)},
                    {"item": "住宿", "cny": round(float(value.lodging), 2)},
                    {"item": "市内交通", "cny": round(float(value.transit), 2)},
                ],
            },
        )

        notes: list[str] = []
        if brief.budget_cny is None or brief.budget_cny <= 0:
            # 金额不写进提示（STOP I5 Q10 教训）：后续 repair 重排会改费用，
            # notes 里的金额不会跟着刷新 —— 陈旧金额进行程单就是「unsupported
            # fact」。权威数字只留费用预估段（reporter 从最终 itinerary 渲染）。
            notes.append(
                "你未提供预算，本次未做预算校验；"
                "费用数据未接入可核验来源，页面不展示估算金额"
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
