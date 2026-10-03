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


def estimate_cost(days, party_size, city="", tier="economy"):
    """费用核算（Optimization 能力）：委托 OptimizationAgent。tier 见 M3-e。"""
    return _optimization.estimate_cost(days, party_size, city=city, tier=tier)


def budget_expert_node(state: dict) -> dict:
    """预算专家节点：填充 CostBreakdown。"""
    def _run(_state: dict) -> dict:
        brief = load_brief(state)
        itinerary = load_itinerary(state)
        if itinerary is None:
            return {"status": "failed", "data": {}, "notes": [],
                    "error": "行程尚未生成，无法核算预算"}

        # 城市档位（P0-3）：餐饮/住宿按目的地消费水平核算，未登记城市回落全局定额；
        # M3-e：按方案档位乘数（TIER_PROFILES.cost_multiplier）核算
        itinerary.cost = run_travel_tool(
            "travel.calculate_budget",
            "optimization",
            lambda: estimate_cost(
                itinerary.days, brief.party_size, city=brief.destination,
                tier=brief.tier),
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

        # ── M3-f 预算协商：预算硬约束（用户拍板口径） ──
        # ① comfortable 且超预算 → 试经济档：排得下则自动降档并明示；
        # ② 经济档也超 → 出缺口数据（最低可行预算+缺口），前端缺口卡协商。
        # 「只算不判」纪律的边界：这里是**档位可行性**判定（决定按哪档出
        # 账），超支判定仍归 validator 轴四。
        negotiation: dict | None = None
        budget = brief.budget_cny
        if budget is not None and budget > 0 and float(itinerary.cost.total) > budget:
            from backend.travel.services import budget_service as _bs

            econ_total = round(float(_bs.estimate_cost(
                itinerary.days, brief.party_size, city=brief.destination,
                tier="economy").total), 2)
            floor = _bs.estimate_budget_floor(
                len(itinerary.days), brief.party_size, city=brief.destination)
            if brief.tier == "comfortable" and econ_total <= budget:
                itinerary.cost = run_travel_tool(
                    "travel.calculate_budget",
                    "optimization",
                    lambda: _bs.estimate_cost(
                        itinerary.days, brief.party_size, city=brief.destination,
                        tier="economy"),
                    result_summary=lambda value: {
                        "total_cny": round(float(value.total), 2),
                        "category": "budget",
                        "tier_downgraded": True,
                    },
                )
                brief.tier = "economy"  # 降档写回（行程与档位保持一致）
                notes_auto = (
                    f"预算 ¥{budget:.0f} 排不出舒适均衡型（约 ¥{econ_total + float(itinerary.cost.total) - econ_total:.0f}），"
                    f"已按经济实用型重排；预算上调至约 ¥{econ_total:.0f} 以上可切回舒适档"
                )
                negotiation = {
                    "tier_downgraded": True,
                    "economy_total_cny": econ_total,
                    "note": notes_auto,
                }
            else:
                gap = round(econ_total if econ_total > budget else float(itinerary.cost.total) - budget, 2)
                negotiation = {
                    "tier_downgraded": False,
                    "floor_total_cny": round(float(floor["total"]), 2),
                    "gap_cny": round(max(0.0, float(floor["total"]) - budget), 2),
                    "note": (
                        f"按当前 {len(itinerary.days)} 天行程，最低需要约 ¥{floor['total']:.0f}"
                        f"（住宿+餐饮硬成本），预算 ¥{budget:.0f} 排不出来"
                    ),
                }

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
    if negotiation:
        update["budget_negotiation"] = negotiation
        update["notes"] = list(update.get("notes", [])) + [negotiation["note"]]
    return update
