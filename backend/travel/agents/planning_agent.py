"""travel/agents/planning_agent.py — Planning Agent（v3 §3.1 ④ / Phase 3 Commit B）

能力边界：must_go 消费下的日程骨架分配（哪天放什么）。无状态：不读写
graph state、无 LLM、不做时刻排程（归 OptimizationAgent）。

依赖纪律：只调 Service（poi_service）；禁止 import providers/tools/
experts/graph_builder；禁止调用其他 Agent。
"""
from __future__ import annotations

from backend.travel.models.brief import TravelBrief
from backend.travel.models.poi import Poi
from backend.travel.services import poi_service
from backend.travel.services.poi_service import Skeleton


class PlanningAgent:
    """Planning Agent：消费 Research 的候选池，产出骨架。无状态，可复用。"""

    def build_skeleton(
        self, brief: TravelBrief, candidates: list[Poi],
    ) -> Skeleton:
        """日程骨架分配（必去置顶/热度/均衡/地理聚类，经 poi_service）。"""
        return poi_service.build_skeleton(brief, candidates)
