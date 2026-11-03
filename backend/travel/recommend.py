"""backend/travel/recommend.py — 目的地推荐（常量热门清单版）

定位：destination 槽位缺失时，给用户一个「可以直接选」的起点，
而不是一句干巴巴的「去哪个城市？」—— 推荐不改变追问语义，
用户仍可自由回答任意城市（境内主要城市经实时地图检索均可规划）。

推荐口径（确定性，无 LLM、无网络）：
  候选池 = HOT_CITIES 常量清单（展示与追问推荐的事实源，人工维护）。
  得分 = 城市偏好标签与用户偏好标签的命中数；命中数降序、同分按清单
  顺序（稳定排序），保证同一输入必然同一推荐 —— 与域图「可复现」的
  总原则一致。

  历史口径迁移（2026-11-03）：旧版候选池取本地种子池（仅福州/厦门/
  杭州），前提是「推荐的城市必须有种子数据才排得出行程」；POI 候选
  自腾讯位置服务实时检索上线后已全国覆盖（TRAVEL_POI_SOURCE=live），
  推荐池随之改为常量清单，追问文案不再受种子池规模限制。前提约束：
  本清单只服务 live 模式 —— 清单上的城市必须都在识别名录
  travel/data/cities.py.CITY_DIRECTORY 内（追问 chips 点击重发后
  extract_destination 才能回填目的地），新增城市时两处同步。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class HotCity:
    """常量清单条目。

    tags 与种子池偏好词表同一口径（人文/自然/美食/亲子/摄影/购物/夜生活），
    命中用户偏好即可在推荐里前置并标注「贴合你的XX偏好」。
    """

    city: str
    highlights: tuple[str, ...]  # 展示用代表性地标（≤3）
    tags: tuple[str, ...] = ()


# 热门城市清单（顺序即无偏好时的展示优先级；南北与海岸线交错铺开，
# 首屏不扎堆一两个区域）。福州/厦门/杭州沿用种子池亮点文案保持连续性。
HOT_CITIES: tuple[HotCity, ...] = (
    HotCity("北京", ("故宫", "八达岭长城", "颐和园"), ("人文", "亲子")),
    HotCity("上海", ("外滩", "豫园", "南京路步行街"), ("人文", "亲子", "购物")),
    HotCity("西安", ("兵马俑", "大唐不夜城", "回民街"), ("人文", "美食")),
    HotCity("成都", ("大熊猫繁育研究基地", "宽窄巷子", "都江堰"), ("亲子", "美食", "自然")),
    HotCity("厦门", ("鼓浪屿", "南普陀寺", "厦门园林植物园"), ("自然", "人文", "美食")),
    HotCity("重庆", ("洪崖洞", "解放碑", "长江索道"), ("美食", "夜生活")),
    HotCity("长沙", ("橘子洲", "岳麓山", "太平老街"), ("美食", "夜生活", "人文")),
    HotCity("三亚", ("亚龙湾", "蜈支洲岛", "天涯海角"), ("自然", "亲子")),
    HotCity("杭州", ("西湖", "灵隐寺", "西溪国家湿地公园"), ("自然", "人文")),
    HotCity("福州", ("三坊七巷", "福建博物院", "鼓山"), ("人文", "自然", "美食")),
)


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
    """按偏好标签为常量热门清单打分排序（纯函数，可单测）。

    Args:
        preferences: 用户偏好标签（TravelBrief.preferences 同源）
        top: 最多返回几个城市
    """
    want = [p for p in preferences if p]

    scored: list[CityRecommendation] = []
    for entry in HOT_CITIES:
        matched = sorted(set(entry.tags) & set(want))
        scored.append(CityRecommendation(
            city=entry.city, score=len(matched),
            matched_preferences=matched,
            highlights=list(entry.highlights),
        ))

    # 命中数降序；list.sort 稳定 —— 同分保持清单顺序，推荐可复现
    scored.sort(key=lambda r: -r.score)
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
