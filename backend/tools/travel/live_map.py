"""tools/travel/live_map.py — 把腾讯位置服务接入旅游域

两处接入，各解决一个 P0 遗留问题：

1. **通勤时长**：``live_leg`` 作为 ``routing.set_route_provider`` 的数据源，
   把「直线距离 × 1.35 ÷ 22km/h」换成腾讯真实路径与路况。
   排程专家、校验器、行程单都无需改动 —— 升级点被收敛在 ``estimate_leg`` 内部。

2. **必去地点缺失**：``resolve_missing_places`` 用腾讯 POI 库把用户点名、
   但本地种子池没有的地点解析成真实坐标补进候选池。P0 遇到这种情况只能在
   备注里写一句「未匹配到」，用户的明确诉求就这么丢了。

**溯源纪律**：腾讯检索不返回营业时间与票价，因此新增的 Poi 只把坐标与名称
当作权威事实（``source="tencent:lbs"``），营业时段沿用契约默认值并如实标注
来源。行程单渲染时会展示 source，用户能分辨哪些是核实过的、哪些是默认值。
"""
from __future__ import annotations

import math
from typing import Iterable

from backend.config import map as MAP
from backend.config import travel as T
from backend.infra.lbs import api
from backend.shared.logger import logger
from backend.tools.travel import routing
from backend.travel.models.poi import (
    CATEGORY_MEAL,
    CATEGORY_NIGHT,
    CATEGORY_PARK,
    CATEGORY_SHOPPING,
    CATEGORY_VISIT,
    Poi,
)

SOURCE_LBS = routing.SOURCE_LIVE

# 原点位解析的安全边界：用户说「福州的土楼」时腾讯可能返回 250km 外的永定土楼，
# 直接塞进行程会造成「一天跨两个城市」的荒谬排程。超出此半径的解析结果丢弃，
# 退回原有的「未匹配到」提示，由对话层去澄清。
_MAX_RESOLVE_DISTANCE_KM = 120.0

# 腾讯 POI 类别（形如 "旅游景点:国家级景点"）→ 本项目类别常量
# 顺序敏感：先匹配具体类别，再落到宽泛的「景点」
_CATEGORY_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("公园", "广场", "绿地", "植物园"), CATEGORY_PARK),
    (("餐饮", "美食", "餐厅", "小吃", "咖啡", "茶艺"), CATEGORY_MEAL),
    (("购物", "商场", "超市", "市场", "商业街"), CATEGORY_SHOPPING),
    (("酒吧", "KTV", "夜总会", "娱乐", "剧院", "演出"), CATEGORY_NIGHT),
    (("景点", "风景", "名胜", "博物", "寺庙", "教堂", "古迹", "文化", "纪念馆"),
     CATEGORY_VISIT),
)


def map_category(tx_category: str) -> str:
    """腾讯 POI 类别 → 本项目类别常量（未识别时按「景点」处理）。"""
    text = tx_category or ""
    for keywords, category in _CATEGORY_RULES:
        if any(k in text for k in keywords):
            return category
    return CATEGORY_VISIT


def is_enabled() -> bool:
    """真实地图数据是否启用（总开关 + Key 均已就绪）。"""
    return MAP.is_live_map_enabled()


# =============================================
# 1. 通勤时长：真实路线
# =============================================
def live_leg(from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> dict | None:
    """真实路线通勤估算，返回与 ``routing.estimate_leg`` 同构的 dict。

    返回 None 表示本段未取到真实数据，由 ``estimate_leg`` 回落本地估算。

    出行方式沿用地铁/步行的既有判定逻辑（``routing.choose_mode``），
    把 "walk" 映射为腾讯的 walking、其余映射为 driving —— 公交（transit）
    不作为默认，因为公交路线在腾讯返回的是「步行 + 地铁 + 公交」多段拼接，
    duration 含候车时间，与「打车/自驾」的口径混用会让行程单前后不一致。
    """
    if not is_enabled():
        return None

    straight = routing.haversine_km(from_lat, from_lng, to_lat, to_lng)
    mode = routing.choose_mode(straight * T.TRAVEL_ROUTE_DETOUR_FACTOR)
    tx_mode = "walking" if mode == "walk" else "driving"

    route = api.direction(tx_mode, from_lat, from_lng, to_lat, to_lng)
    if route is None or not route.get("distance_km"):
        return None

    distance_km = float(route["distance_km"])
    # 分钟取整向上：宁可让用户早到 1 分钟，也不要让他赶不上
    minutes = max(routing.MIN_LEG_MINUTES.get(mode, 5),
                  int(math.ceil(float(route.get("duration_min") or 0))))

    if mode == "walk":
        cost = 0.0
    else:
        # 腾讯返回的 taxi_fare 是真实计程车价，比「起步价 + 里程费」的
        # 拍脑袋模型准；未返回时退回本地模型。
        taxi = route.get("taxi_fare_cny")
        cost = float(taxi) if taxi else routing.leg_cost_cny(distance_km, mode)

    return {
        "distance_km": round(distance_km, 2),
        "mode": mode,
        "minutes": minutes,
        "cost_cny": round(cost, 2),
        "source": SOURCE_LBS,
    }


def install_live_map() -> bool:
    """按配置接入真实路线数据源（幂等）。

    Returns:
        True 表示已接入，False 表示未启用（保持本地估算）。
    """
    if not is_enabled():
        logger.info("[TravelLiveMap] 未启用（TRAVEL_USE_LIVE_MAP=%s, key=%s）",
                    MAP.TRAVEL_USE_LIVE_MAP, bool(MAP.TENCENT_LBS_KEY))
        routing.set_route_provider(None)
        return False
    routing.set_route_provider(live_leg)
    logger.info("[TravelLiveMap] 已接入腾讯位置服务真实路线规划")
    return True


# =============================================
# 2. 必去地点解析：补齐本地候选池的缺口
# =============================================
def _city_center(city: str) -> tuple[float, float] | None:
    """取城市中心坐标，用于距离安全边界判定。"""
    district = api.resolve_district(city)
    if district and (district["lat"] or district["lng"]):
        return district["lat"], district["lng"]
    return None


def _straight_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    return routing.haversine_km(a[0], a[1], b[0], b[1])


def resolve_place(name: str, city: str, *, required: bool = False) -> Poi | None:
    """把一个地点名解析为 Poi（腾讯 POI 库）。

    Args:
        name: 用户点名的地点，如 "平潭岛"、"鼓岭"
        city: 所属城市，用于限定检索范围与「城市键」对齐
        required: 是否标记为「用户点名必去」。

            **必须由调用方显式传入**：``poi_expert`` 的骨架分配按
            ``(not required, -rating, poi_id)`` 排序，而补入的 Poi 热度恒为 0，
            不标 required 就会排在所有种子 POI 之后，被节奏容量一挤就丢 ——
            用户明确点名要去的地方就这么消失了（实测踩过）。

    Returns:
        Poi（source="tencent:lbs"）；解析不到或超出安全半径时返回 None。
    """
    if not is_enabled() or not (name or "").strip():
        return None

    hits = api.place_search(name.strip(), region=city or None, page_size=5)
    if not hits:
        logger.info("[TravelLiveMap] 腾讯未检索到地点: %s（城市=%s）", name, city)
        return None

    center = _city_center(city) if city else None
    for hit in hits:
        lat, lng = hit.get("lat"), hit.get("lng")
        if not lat or not lng:
            continue
        if center is not None:
            gap = _straight_km(center, (lat, lng))
            if gap > _MAX_RESOLVE_DISTANCE_KM:
                logger.info(
                    "[TravelLiveMap] 解析结果「%s」距 %s 中心 %.0fkm，超出 %.0fkm 安全半径，丢弃",
                    hit["name"], city, gap, _MAX_RESOLVE_DISTANCE_KM,
                )
                continue
        # 城市键要与 TravelBrief.destination 对齐：能归一就用归一结果
        from backend.tools.travel.poi import resolve_city

        city_key = resolve_city(city) or city or hit.get("city") or ""
        return Poi(
            # 用腾讯 POI id 构造稳定标识，跨会话可复现
            poi_id=f"lbs_{hit['id'] or abs(hash((name, city))) % 10**12}",
            name=hit["name"],
            city=city_key,
            category=map_category(hit.get("category", "")),
            lat=float(lat),
            lng=float(lng),
            # 营业时段腾讯检索不提供，沿用契约默认值；
            # source 已如实标注，行程单会显示来源，不会伪装成核实事实。
            open_time="09:00",
            close_time="17:00",
            suggested_minutes=120,
            ticket_cny=0.0,
            tags=[],
            rating=0.0,
            required=required,
            source=SOURCE_LBS,
        )
    return None


def resolve_missing_places(
    city: str, candidates: Iterable[Poi], wanted_names: Iterable[str],
) -> tuple[list[Poi], list[str]]:
    """把候选池里缺失的点名地点，用腾讯 POI 库补全。

    Args:
        city: 目的地城市
        candidates: 当前候选池（用于判断「已存在」）
        wanted_names: 用户点名要去的名称列表（brief.must_go）

    Returns:
        (新增的 Poi 列表, 说明文案列表)
    """
    existing = list(candidates)
    added: list[Poi] = []
    notes: list[str] = []

    for raw in wanted_names:
        name = (raw or "").strip()
        if not name:
            continue
        if any(name in p.name or p.name in name for p in existing + added):
            continue  # 候选池里已有，无需补
        # required=True：这些名字全部来自 brief.must_go，用户已明确点名要去的
        poi = resolve_place(name, city, required=True)
        if poi is None:
            continue
        added.append(poi)
        notes.append(
            f"「{poi.name}」不在本地候选数据中，已通过腾讯位置服务解析其真实坐标后补入"
            f"（类别「{poi.category}」；营业时间与票价未核实，请出行前确认）"
        )

    if added:
        logger.info("[TravelLiveMap] 为 %s 补入 %d 个地点: %s",
                    city, len(added), [p.name for p in added])
    return added, notes
