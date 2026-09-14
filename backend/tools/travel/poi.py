"""tools/travel/poi.py — POI 检索与候选筛选

分层职责：
  search_poi()  — 纯函数，可单测：城市归一 → 排除 → 打分 → 截断
  travel_poi_search_tool — LangChain Tool 封装，供 Skill 层与 function calling 使用

**为什么粗筛不放给 LLM**：候选池动辄数十条，把全量 POI 塞进 prompt 既贵
又容易让模型漏项。此处用规则打分把候选压到 20 条以内，LLM（P1）只负责
在候选池里做组合取舍 —— 与 CS 的「规则预判 + LLM 兜底」是同一成本纪律。
"""
from __future__ import annotations

import json

from langchain_core.tools import tool

from backend.shared.logger import logger
from backend.tools.travel import poi_seed
from backend.travel.models.poi import Poi

# 打分权重：必去项必须无条件入选，故给足以碾压任何组合的权重
_W_MUST_GO = 1000.0
_W_TAG = 10.0
_W_RATING = 1.0


def resolve_city(raw: str) -> str | None:
    """把用户输入的模糊地名归一到数据集的城市键。

    依次尝试：精确匹配 → 别名表 → 双向子串包含（"福州市区" → "福州"）。
    无法归一时返回 None —— 由调用方决定降级方式，本函数不猜。
    """
    if not raw:
        return None
    key = raw.strip().lower()
    cities = poi_seed.all_cities()
    for c in cities:
        if c.lower() == key:
            return c
    if key in poi_seed.CITY_ALIASES:
        return poi_seed.CITY_ALIASES[key]
    for c in cities:
        if c.lower() in key:
            return c
    for c in cities:
        if key in c.lower():
            return c
    return None


def score_poi(poi: Poi, preferences: list[str], must_go: list[str]) -> float:
    """候选打分：必去 > 偏好标签命中 > 热度。"""
    score = 0.0
    if _matches_name(poi, must_go):
        score += _W_MUST_GO
    hits = sum(1 for tag in poi.tags if tag in preferences)
    score += hits * _W_TAG
    score += poi.rating * _W_RATING
    return score


def _matches_name(poi: Poi, names: list[str]) -> bool:
    """POI 名与用户给定名称的宽松匹配（用户常说简称或带城市前缀）。"""
    for n in names:
        needle = (n or "").strip()
        if not needle:
            continue
        if needle in poi.name or poi.name in needle:
            return True
    return False


def is_excluded(poi: Poi, avoid: list[str]) -> bool:
    """命中避雷清单则整条排除（名称 / 类别 / 标签任一命中即排除）。

    避雷是用户的硬要求，不做「命中但分数低」这种暧昧处理。
    """
    for a in avoid:
        needle = (a or "").strip()
        if not needle:
            continue
        if needle in poi.name or needle == poi.category or needle in poi.tags:
            return True
    return False


def search_poi(
    city: str,
    preferences: list[str] | None = None,
    avoid: list[str] | None = None,
    must_go: list[str] | None = None,
    limit: int = 20,
) -> list[Poi]:
    """检索候选 POI（按分数降序）。

    Args:
        city: 目的地（支持模糊，经 resolve_city 归一）
        preferences: 偏好标签，命中 tag 加分
        avoid: 避雷清单，命中的条目直接剔除
        must_go: 必去清单，命中的条目打 required 标记并置顶
        limit: 返回上限

    Returns:
        Poi 列表；城市无法归一或数据源为空时返回 []
    """
    preferences = preferences or []
    avoid = avoid or []
    must_go = must_go or []

    city_key = resolve_city(city)
    if city_key is None:
        logger.info("[TravelPOI] 城市未归一: %r（已知: %s）", city, poi_seed.all_cities())
        return []

    pool = [p for p in poi_seed.load_city(city_key) if not is_excluded(p, avoid)]
    for p in pool:
        p.required = _matches_name(p, must_go)

    pool.sort(key=lambda p: (-score_poi(p, preferences, must_go), p.poi_id))
    if limit > 0:
        pool = pool[:limit]

    logger.info(
        "[TravelPOI] city=%s 候选=%d（偏好=%s 必去=%s 排除=%s）",
        city_key, len(pool), preferences, must_go, avoid,
    )
    return pool


@tool
def travel_poi_search_tool(
    city: str,
    preferences: str = "",
    avoid: str = "",
    must_go: str = "",
    limit: int = 20,
) -> str:
    """
    检索旅行目的地城市的候选兴趣点（POI），返回带营业时段、坐标、票价的 JSON 列表。
    city: 目的地城市名，如 "福州"、"厦门"、"杭州"
    preferences: 逗号分隔的偏好标签，可选值：自然/人文/美食/亲子/购物/夜生活/摄影
    avoid: 逗号分隔的避雷关键词（地名或类别），命中即从候选中剔除
    must_go: 逗号分隔的必去地点名，命中项会被标记 required=true 并优先返回
    limit: 返回条数上限（默认 20）
    适用场景：规划行程前拉取候选景点池。注意返回的是候选池，不含排程与时间校验。
    """
    def _split(raw: str) -> list[str]:
        return [s.strip() for s in raw.replace("，", ",").split(",") if s.strip()]

    pois = search_poi(
        city=city,
        preferences=_split(preferences),
        avoid=_split(avoid),
        must_go=_split(must_go),
        limit=limit,
    )
    if not pois:
        known = "、".join(poi_seed.all_cities())
        return json.dumps(
            {"error": f"未找到城市「{city}」的 POI 数据", "known_cities": known},
            ensure_ascii=False,
        )
    return json.dumps(
        {"city": pois[0].city, "count": len(pois),
         "pois": [p.model_dump() for p in pois]},
        ensure_ascii=False,
    )


# ==================== Tool Registry 自动注册 ====================
from backend.tools.tool_registry import tool_registry
tool_registry.register(travel_poi_search_tool, __file__)
