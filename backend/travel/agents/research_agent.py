"""travel/agents/research_agent.py — Research Agent（v3 §3.1 ③ / Phase 3 Commit B）

能力边界：数据获取与事实准备——候选检索（含必去项补全）、天气预报获取、
风险溯源、知识库摘录。无状态：不读写 graph state（归图节点）、无 LLM。

依赖纪律：只调 Service（poi/weather/risk_service），**禁止 import
providers/tools**（Provider 触达点已收敛在 service 层，Phase 4
ProviderRouter 在 service 内插入）；禁止 import experts/graph_builder；
禁止调用其他 Agent。
"""
from __future__ import annotations

from backend.travel.models.brief import TravelBrief
from backend.travel.models.poi import Poi
from backend.travel.services import poi_service, risk_service, weather_service


class ResearchAgent:
    """Research Agent：Planning/Optimization 的数据供给方。无状态，可复用。"""

    def retrieve_candidates(
        self, brief: TravelBrief,
    ) -> tuple[list[Poi], list[str]]:
        """候选池检索 + 必去项 Provider 补全（经 poi_service）。"""
        return poi_service.retrieve_candidates(brief)

    def fetch_forecast(self, destination: str) -> tuple[dict | None, str]:
        """天气预报获取与七态降级映射（经 weather_service）。"""
        return weather_service.fetch_forecast(destination)

    def fetch_forecast_evidence(
        self, destination: str,
    ) -> tuple[dict | None, str, dict | None]:
        """天气预报获取 + Evidence 组装（Phase 4 三元组通道，经
        weather_service；Evidence 在 service 层组装，provider 层零改动）。"""
        return weather_service.fetch_forecast_evidence(destination)

    def build_candidate_evidences(self, candidates: list[Poi]) -> dict[str, dict]:
        """候选池证据表（Phase 4，经 poi_service 纯函数派生）。"""
        return poi_service.build_candidate_evidences(candidates)

    def build_knowledge_evidence(
        self, destination: str, chunks: list[str], source_tag: str,
    ) -> dict[str, dict]:
        """知识摘录证据表（Phase 4，经 risk_service 纯函数派生）。"""
        return risk_service.build_knowledge_evidence(
            destination, chunks, source_tag)

    def assess_risks(self, itinerary) -> tuple[list[str], list[str]]:
        """行程溯源与能力边界披露（经 risk_service）。"""
        return risk_service.assess_risks(itinerary)

    def retrieve_knowledge(
        self, destination: str, preferences: list[str],
    ) -> tuple[list[str], str]:
        """知识库摘录（软失败，经 risk_service）。"""
        return risk_service.retrieve_knowledge(destination, preferences)
