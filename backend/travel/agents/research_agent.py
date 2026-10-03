"""travel/agents/research_agent.py — Research Agent（v3 §3.1 ③ / Phase 3 Commit B）

能力边界：数据获取与事实准备——候选检索（含必去项补全）、天气预报获取、
风险溯源、知识库摘录。无状态：不读写 graph state（归图节点）、无 LLM。

依赖纪律：只调 Service（poi/weather/risk_service），**禁止 import
providers/tools**（Provider 触达点已收敛在 service 层，Phase 4
ProviderRouter 在 service 内插入）；禁止 import experts/graph_builder；
禁止调用其他 Agent。
"""
from __future__ import annotations

import re

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
        """知乎攻略检索（2026-10-03 多主题）。

        规划主链自动触发（不再依赖用户消息含「攻略」触发词）：知乎站内按
        景点/美食/城市特色三主题各查一次 + 全网城市攻略一路；结果供 SSE
        攻略卡展示与候选 POI 提及理由匹配。

        攻略是增强信息非规划硬依赖：单主题/单路失败独立降级为
        ``{"error": ...}``，绝不让一路失败拖垮 poi 专家节点；全失败时
        返回 error，交付端据此披露「攻略检索不可用」。
        """
        by_topic: dict[str, list] = {}
        errors: list[str] = []
        for topic in live_search_service.GUIDE_TOPIC_QUERIES:
            try:
                payload = live_search_service.search_zhihu_guides(
                    destination=destination, topic=topic)
            except LiveSearchError as exc:
                logger.warning("[ResearchAgent] 攻略检索 %s/%s 失败: %s",
                               destination, topic, exc)
                errors.append(str(exc))
                continue
            for item in payload.get("results") or []:
                if isinstance(item, dict):
                    by_topic.setdefault(topic, []).append({**item, "topic": topic})
        # 三主题轮转交错：preview 截断（[:6]）后仍三主题均衡可见
        interleaved: list[dict] = []
        cursors: dict[str, int] = {}
        while True:
            advanced = False
            for topic, items in by_topic.items():
                i = cursors.get(topic, 0)
                if i < len(items):
                    interleaved.append(items[i])
                    cursors[topic] = i + 1
                    advanced = True
            if not advanced:
                break

        guides: dict = {}
        guides["zhihu"] = (
            {"results": interleaved} if interleaved else {"error": "；".join(dict.fromkeys(errors))}
        )
        try:
            guides["web"] = live_search_service.search_web_guides(destination=destination)
        except LiveSearchError as exc:
            logger.warning("[ResearchAgent] 攻略检索 web 路失败: %s", exc)
            guides["web"] = {"error": str(exc)}
        return guides

    @staticmethod
    def match_guide_mentions(candidates: list[Poi], guides: dict) -> dict[str, str]:
        """知乎攻略提及匹配：POI 全名出现在攻略标题/摘要 → 入选理由。

        纯文本包含匹配（保守口径：不做分词/近似匹配，宁缺勿错——
        matched 是要展示给用户的推荐依据，错了就是编造）。两字以下
        名称不参与（子串误命中率过高）。
        """
        texts: list[tuple[str, str]] = []
        for payload in (guides or {}).values():
            if not isinstance(payload, dict) or payload.get("error"):
                continue
            for item in payload.get("results") or []:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()
                if not title:
                    continue
                texts.append((title, f"{title}\n{item.get('summary') or ''}"))
        if not texts:
            return {}
        mentions: dict[str, str] = {}
        for poi in candidates:
            name = ResearchAgent._normalize_name(poi.name)
            if len(name) < 2:
                continue
            for title, haystack in texts:
                if name in haystack:
                    mentions[poi.poi_id] = f"知乎攻略《{title}》提及"
                    break
        return mentions

    @staticmethod
    def _normalize_name(name: str) -> str:
        """名称规范化：去空白与中英文括号内容（「西湖（杭州景区）」→「西湖」）。"""
        text = re.sub(r"[（(].*?[)）]", "", name or "")
        return re.sub(r"\s+", "", text)

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
