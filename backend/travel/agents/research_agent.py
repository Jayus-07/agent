"""travel/agents/research_agent.py — Research Agent（v3 §3.1 ③ / Phase 3 Commit B）

能力边界：数据获取与事实准备——候选检索（含必去项补全）、天气预报获取、
风险溯源、知识库摘录。无状态：不读写 graph state（归图节点）、无 LLM。

依赖纪律：只调 Service（poi/weather/risk_service），**禁止 import
providers/tools**（Provider 触达点已收敛在 service 层，Phase 4
ProviderRouter 在 service 内插入）；禁止 import experts/graph_builder；
禁止调用其他 Agent。
"""
from __future__ import annotations

from backend.shared.logger import logger
from backend.travel.models.brief import TravelBrief
from backend.travel.models.poi import Poi
from backend.travel.services import (
    live_search_service,
    poi_service,
    risk_service,
    weather_service,
)
from backend.travel.services.live_search_service import LiveSearchError


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

    def search_food(self, city: str) -> dict:
        return live_search_service.search_food(city)

    def search_hotels(self, city: str) -> dict:
        return live_search_service.search_hotels(city)

    def search_guides(self, destination: str) -> dict:
        """旅游/美食攻略检索（知乎官方 MCP 两路）。

        攻略是增强信息非规划硬依赖：单路失败独立降级为 ``{"error": ...}``，
        绝不让一路失败拖垮 poi 专家节点；全失败时返回两路 error，交付端
        据此披露「攻略检索不可用」。
        """
        guides: dict = {}
        for name, fetch in (
            ("zhihu", live_search_service.search_zhihu_guides),
            ("web", live_search_service.search_web_guides),
        ):
            try:
                guides[name] = fetch(destination=destination)
            except LiveSearchError as exc:
                logger.warning("[ResearchAgent] 攻略检索 %s 路失败: %s", name, exc)
                guides[name] = {"error": str(exc)}
        return guides

    @staticmethod
    def guide_event_summary(data: dict) -> dict:
        """两路攻略合并结果的 SSE 摘要（单路失败保留失败标记）。"""
        merged: dict = {"category": "guide", "preview": [], "provider": "zhihu_mcp"}
        counts = []
        for name, payload in (data or {}).items():
            if not isinstance(payload, dict) or payload.get("error"):
                merged[f"{name}_status"] = "unavailable"
                continue
            results = payload.get("results") or []
            counts.append(len(results))
            merged["preview"].extend(
                live_search_service.guides_preview(payload, name)["preview"][:4])
            merged[f"{name}_status"] = "available" if results else "empty"
        merged["result_count"] = sum(counts)
        merged["data_status"] = "available" if any(counts) else (
            "empty" if merged.get("zhihu_status") != "unavailable" else "unavailable")
        return merged

    @staticmethod
    def merchant_event_summary(data: dict, category: str) -> dict:
        return live_search_service.merchant_preview(data, category)
