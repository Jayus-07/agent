"""travel/agents/planning_agent.py — Planning Agent（travel-domain-design-v5.md §10 ④ / Phase 3 Commit B）

能力边界：must_go 消费下的日程骨架分配（哪天放什么）。无状态：不读写
graph state、无 LLM、不做时刻排程（归 OptimizationAgent）。

依赖纪律：只调 Service（poi_service）；禁止 import providers/tools/
experts/graph_builder；禁止调用其他 Agent。
"""
from __future__ import annotations

from backend.travel.models.brief import TravelBrief
from backend.travel.models.poi import Poi
from backend.travel.services import live_search_service, poi_service
from backend.travel.services.poi_service import Skeleton


class PlanningAgent:
    """Planning Agent：消费 Research 的候选池，产出骨架。无状态，可复用。"""

    def build_skeleton(
        self, brief: TravelBrief, candidates: list[Poi],
    ) -> Skeleton:
        """日程骨架分配（必去置顶/热度/均衡/地理聚类，经 poi_service）。"""
        return poi_service.build_skeleton(brief, candidates)

    def search_trains(self, *, from_station: str, to_station: str,
                      travel_date: str, limit: int = 6) -> dict:
        return live_search_service.search_trains(
            from_station=from_station,
            to_station=to_station,
            travel_date=travel_date,
            limit=limit,
        )

    def attach_train_prices(
        self, train_data: dict, *, from_station: str, to_station: str,
        travel_date: str, limit: int = 2,
    ) -> dict:
        """对前 limit 个车次并查票价，结果并入 trains[i]["prices"]。

        票价是独立上游工具、一次一车次：单次失败只降级该行（不写价格、
        不重试），绝不让票价失败拖垮车票数据本体；展示端对没查到的行
        显示占位符。出行前报价请以 12306 官方为准（非官方聚合源）。
        """
        trains = train_data.get("trains") or []
        for train in trains[:max(0, limit)]:
            if not isinstance(train, dict):
                continue
            code = str(train.get("train_no") or "").strip()
            if not code:
                continue
            try:
                payload = live_search_service.search_train_price(
                    from_station=from_station,
                    to_station=to_station,
                    travel_date=travel_date,
                    train_code=code,
                )
            except live_search_service.LiveSearchError as exc:
                from backend.shared.logger import logger

                logger.warning("[PlanningAgent] 车次 %s 票价查询失败（降级跳过）: %s",
                               code, exc)
                continue
            prices = payload.get("prices")
            if isinstance(prices, dict) and prices:
                train["prices"] = prices
        return train_data

    @staticmethod
    def train_event_summary(data: dict) -> dict:
        return live_search_service.train_preview(data)
