"""travel/agents/optimization_agent.py — Optimization Agent（v3 §3.1 ⑤ / Phase 3 Commit B）

能力边界：把骨架变成定稿——时刻排程与重排（rebuild_days 唯一实现经
transit_service）、坏天气适应（Plan B 替换）、费用核算。无状态：不读写
graph state、无 LLM。2-opt 路径优化为后续增强位（route.optimizer 接口），
本阶段算法保持最近邻零改动。

依赖纪律：只调 Service（transit/weather/budget_service）；禁止 import
providers/tools/experts/graph_builder；禁止调用其他 Agent。
"""
from __future__ import annotations

from backend.travel.models.brief import TravelBrief
from backend.travel.models.poi import Poi
from backend.travel.services import (
    budget_service,
    transit_service,
    weather_service,
)


class OptimizationAgent:
    """Optimization Agent：骨架 → 定稿行程。无状态，可复用。"""

    def prefetch_day_legs(self, pois_by_day: list[list[Poi]]) -> None:
        """并行预热同日相邻路线（best-effort，经 transit_service）。"""
        transit_service.prefetch_day_legs(pois_by_day)

    def build_itinerary(
        self, brief: TravelBrief, pois_by_day: list[list[Poi]],
    ) -> tuple[object, list[str]]:
        """骨架 → 带时刻的完整行程（空白天压缩，经 transit_service）。"""
        return transit_service.build_itinerary(brief, pois_by_day)

    def plan_weather_swaps(
        self, itinerary, candidates: list[dict], bad_dates: list[str],
    ) -> tuple[object, list[dict], list[str]]:
        """坏天气日户外→室内替换并重排（必去永不换，经 weather_service）。"""
        return weather_service.plan_weather_swaps(itinerary, candidates, bad_dates)

    def estimate_cost(self, days, party_size: int, city: str = ""):
        """费用核算（只算不判，经 budget_service）。"""
        return budget_service.estimate_cost(days, party_size, city=city)
