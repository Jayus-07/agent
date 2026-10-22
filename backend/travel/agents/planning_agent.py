"""旅游 Planning Agent：LLM 提出逐日安排，确定性服务校验约束并执行回退。

能力边界：基于真实候选为地点提出逐日安排；不读写 graph state，不调用
Provider/Tool，不做时刻排程（归 OptimizationAgent）。模型不可用、输出越界或
硬约束不满足时使用既有确定性骨架算法。

依赖纪律：只调 Service（poi_service）；禁止 import providers/tools/
experts/graph_builder；禁止调用其他 Agent。
"""
from __future__ import annotations

from backend.config import travel
from backend.travel.models.brief import TravelBrief
from backend.travel.models.poi import Poi, is_pure_meal
from backend.travel.planning import names_match
from backend.travel.services import (
    live_search_service,
    llm_planning_service,
    poi_service,
)
from backend.travel.services.poi_service import Skeleton


class PlanningAgent:
    """Planning Agent：消费 Research 的候选池，产出骨架。无状态，可复用。"""

    def build_skeleton(
        self, brief: TravelBrief, candidates: list[Poi],
    ) -> Skeleton:
        """LLM 决定候选的目标日；代码负责硬约束、骨架构造和规则回退。"""
        fallback = poi_service.build_skeleton(brief, candidates)
        candidate_ids = [poi.poi_id for poi in candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            fallback.planner_decision = {
                "status": "fallback",
                "fallback_reason": "duplicate_candidate_id",
                "has_proposal": False,
            }
            return fallback

        required_ids = {
            poi.poi_id for poi in candidates
            if poi.required or any(
                names_match(poi.name, wanted) for wanted in brief.must_go
            )
        }
        eligible = [
            poi for poi in candidates
            if not is_pure_meal(poi) or poi.poi_id in required_ids
        ]
        if not eligible:
            fallback.planner_decision = {
                "status": "skipped",
                "fallback_reason": "no_schedulable_candidates",
                "candidate_count": 0,
                "has_proposal": False,
            }
            return fallback

        pace = brief.normalized_pace()
        context = {
            "brief": {
                "destination": brief.destination,
                "days": brief.resolved_days(),
                "pace": pace,
                "party_size": brief.party_size,
                "preferences": list(brief.preferences or []),
                "must_go": list(brief.must_go or []),
                "optional_go": list(brief.optional_go or []),
                "avoid": list(brief.avoid or []),
            },
            "constraints": {
                "max_pois_per_day": travel.TRAVEL_PACE_MAX_POIS.get(pace, 5),
                "max_activity_minutes_per_day": travel.TRAVEL_PACE_MINUTES.get(
                    pace, 360,
                ),
                "single_long_activity_may_occupy_a_day": True,
            },
            "candidates": [
                {
                    "poi_id": poi.poi_id,
                    "name": poi.name,
                    "category": poi.category,
                    "tags": list(poi.tags or []),
                    "rating": poi.rating,
                    "suggested_minutes": poi.suggested_minutes,
                    "lat": poi.lat,
                    "lng": poi.lng,
                    "required": poi.poi_id in required_ids,
                    "optional": any(
                        names_match(poi.name, wanted)
                        for wanted in brief.optional_go
                    ),
                }
                for poi in eligible
            ],
        }
        outcome = llm_planning_service.propose_day_plan(context)
        if outcome.proposal is None:
            fallback.planner_decision = outcome.meta()
            return fallback

        rejection = self._proposal_rejection_reason(
            brief, eligible, required_ids, outcome.proposal, fallback,
        )
        if rejection:
            outcome.status = "rejected"
            outcome.fallback_reason = rejection
            fallback.planner_decision = outcome.meta()
            return fallback

        preferred_day_by_poi = {
            poi_id: day_index
            for day_index, day in enumerate(outcome.proposal.days, start=1)
            for poi_id in day
        }
        try:
            planned = poi_service.build_skeleton(
                brief,
                candidates,
                preferred_day_by_poi=preferred_day_by_poi,
                excluded_poi_ids=set(outcome.proposal.dropped_poi_ids),
            )
        except ValueError:
            outcome.status = "rejected"
            outcome.fallback_reason = "proposal_execution_rejected"
            fallback.planner_decision = outcome.meta()
            return fallback
        planned.planner_decision = outcome.meta()
        return planned

    @staticmethod
    def _proposal_rejection_reason(
        brief: TravelBrief,
        candidates: list[Poi],
        required_ids: set[str],
        proposal: llm_planning_service.TravelDayPlanProposal,
        fallback: Skeleton,
    ) -> str:
        if len(proposal.days) != brief.resolved_days():
            return "day_count_mismatch"

        candidate_map = {poi.poi_id: poi for poi in candidates}
        candidate_ids = set(candidate_map)
        assigned = [poi_id for day in proposal.days for poi_id in day]
        dropped = proposal.dropped_poi_ids
        if len(assigned) != len(set(assigned)) or len(dropped) != len(set(dropped)):
            return "duplicate_poi_assignment"
        if set(assigned) & set(dropped):
            return "poi_both_assigned_and_dropped"
        if set(assigned) | set(dropped) != candidate_ids:
            return "candidate_coverage_mismatch"
        if required_ids & set(dropped):
            return "required_poi_dropped"
        if len(dropped) > len(fallback.dropped):
            return "excess_candidates_dropped"

        pace = brief.normalized_pace()
        max_pois = travel.TRAVEL_PACE_MAX_POIS.get(pace, 5)
        max_minutes = travel.TRAVEL_PACE_MINUTES.get(pace, 360)
        for day in proposal.days:
            if len(day) > max_pois:
                return "daily_poi_capacity_exceeded"
            minutes = sum(
                candidate_map[poi_id].suggested_minutes for poi_id in day
            )
            if len(day) > 1 and minutes > max_minutes:
                return "daily_duration_capacity_exceeded"
        return ""

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
