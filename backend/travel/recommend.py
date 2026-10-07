"""backend/travel/recommend.py — 目的地推荐（P1-3 轻量规则版）

定位：destination 槽位缺失时，给用户一个「可以直接选」的起点，
而不是一句干巴巴的「去哪个城市？」—— 推荐不改变追问语义，
用户仍可自由回答任意城市（支持的进规划，不支持的如实告知）。

推荐口径（确定性，无 LLM、无网络）：
  得分 = 城市内候选 POI 与用户偏好标签的命中数（按热度取前几名做展示）
  同分按城市键排序，保证同一输入必然同一推荐 —— 与域图「可复现」的
  总原则一致。数据源就是本地种子池：推荐哪个城市的前提是那个城市
  真的排得出行程，种子池覆盖之外的城市不推荐（避免推荐了却排不了）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.tools.travel import poi_seed


@dataclass
class CityRecommendation:
    """单个城市的推荐结果（可 JSON 序列化，供 API 与追问文案共用）"""

    city: str
    score: int
    matched_preferences: list[str] = field(default_factory=list)
    highlights: list[str] = field(default_factory=list)  # 展示用 POI 名（≤3）

    def to_dict(self) -> dict:
        return {
            "city": self.city,
            "score": self.score,
            "matched_preferences": self.matched_preferences,
            "highlights": self.highlights,
        }


def recommend_cities(
    preferences: list[str], top: int = 3,
) -> list[CityRecommendation]:
    """按偏好标签为种子池城市打分排序（纯函数，可单测）。

    Args:
        preferences: 用户偏好标签（TravelBrief.preferences 同源）
        top: 最多返回几个城市
    """
    want = [p for p in preferences if p]
    if not want:
        # 无偏好时按「城市 POI 热度总和」给一份默认推荐，仍保证确定性
        want = []

    scored: list[CityRecommendation] = []
    for city in poi_seed.all_cities():
        pois = poi_seed.load_city(city)
        matched: set[str] = set()
        score = 0
        for poi in pois:
            hits = [t for t in poi.tags if t in want]
            if hits:
                score += len(hits) * max(1.0, poi.rating)
                matched.update(hits)
        # 展示亮点：按热度取前 3（无偏好时就是纯热度榜）
        highlights = [p.name for p in
                      sorted(pois, key=lambda p: (-p.rating, p.poi_id))[:3]]
        scored.append(CityRecommendation(
            city=city, score=round(score, 1), matched_preferences=sorted(matched),
            highlights=highlights,
        ))

    scored.sort(key=lambda r: (-r.score, r.city))
    if not want:
        # 无偏好：分数全为 0，回退为纯热度展示，城市顺序仍稳定
        for r in scored:
            r.score = 0
    return scored[:max(1, top)]


def render_recommendation_line(recs: list[CityRecommendation]) -> str:
    """推荐结果 → 追问文案里的一行（供 build_clarification 拼接）。

    口径：所有推荐都未命中偏好标签（典型=新用户无偏好，纯热度榜）时，
    前缀改用「热门城市」，避免「根据你的偏好」的无个性化误导（2026-10-07 实测反馈）。
    """
    if not recs:
        return ""
    parts: list[str] = []
    for r in recs:
        if r.matched_preferences:
            parts.append(
                f"{r.city}（{'/'.join(r.highlights)}，"
                f"贴合你的{'、'.join(r.matched_preferences)}偏好）"
            )
        else:
            parts.append(f"{r.city}（{'/'.join(r.highlights)}）")
    personalized = any(r.matched_preferences for r in recs)
    prefix = "根据你的偏好，可以先看看：" if personalized else "可以先看看这些热门城市："
    return prefix + "；".join(parts)
