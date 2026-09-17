"""tools/travel/routing.py — 地理与通勤估算（纯函数 + 可插拔真实数据源）

P0 口径：直线距离 × 绕行系数 ÷ 速度模型 ⇒ 通勤分钟数。
这是**估算**而非导航结果，因此：
  - 所有系数集中在 config/travel.py，可随城市/数据源整体调参
  - 输出附带 distance_km，行程单可展示依据

P1 口径（本文件已实现插槽）：通过 ``set_route_provider`` 注入真实路线数据源
（见 ``tools/travel/live_map.py`` 对腾讯位置服务的适配）。注入后
``estimate_leg`` 优先走真实路径与路况，**失败自动回落纯函数估算**，
因此上游（transit 专家 / validator / reporter）契约完全不变。

为什么只让 ``estimate_leg`` 用真实数据，而 ``route_km`` 保持纯：
  - ``route_km`` 是**排序启发式**（最近邻串联、折返检测），每轮调用 O(n²) 次，
    走网络会把一次排程变成上百次请求；且排序对距离精度不敏感。
  - ``estimate_leg`` 是**写进行程单的展示值**，精度直接影响用户体感与校验结论，
    必须用真实数据。
"""
from __future__ import annotations

import inspect
import math
from datetime import date
from functools import lru_cache
from typing import Callable

from backend.config import travel as T
from backend.shared.logger import logger

EARTH_RADIUS_KM = 6371.0088

# 短距离步行的最小耗时（含等灯/找入口），纯公式会算出 2 分钟这种不现实的值
MIN_LEG_MINUTES = {"walk": 5, "drive": 8}
# 兼容旧引用
_MIN_MINUTES = MIN_LEG_MINUTES

# 真实路线数据源签名：(from_lat, from_lng, to_lat, to_lng[, trip_date]) -> estimate_leg 同构 dict | None
# Phase 1 起 provider 可声明可选 kw-only 参数 trip_date（providers/travel 远期降级策略）；
# 旧式 4 位置参数 provider（如 live_leg、测试桩）依然兼容 —— 见 _accepts_trip_date。
RouteProvider = Callable[..., "dict | None"]

# 数据来源标识，与 travel.models.itinerary.TransitLeg.source 的取值对齐
SOURCE_LOCAL = "estimate:local"
SOURCE_LIVE = "tencent:lbs"

_route_provider: RouteProvider | None = None


def set_route_provider(provider: RouteProvider | None) -> None:
    """注入/清除真实路线数据源（幂等）。

    provider 返回 None 表示「本次查不到」，此时 estimate_leg 走本地估算，
    因此数据源不需要自己实现降级逻辑。
    """
    global _route_provider
    _route_provider = provider
    logger.info("[TravelRouting] 路线数据源已%s",
                "接入：" + (getattr(provider, "__name__", str(provider)) if provider else "")
                if provider else "清除，回退本地直线估算")


def get_route_provider() -> RouteProvider | None:
    return _route_provider


@lru_cache(maxsize=8)
def _accepts_trip_date(provider: RouteProvider) -> bool:
    """provider 是否声明了 trip_date 参数（决定 estimate_leg 是否透传出行日期）。

    旧式 4 参 provider（live_leg、测试桩）不认识 trip_date，硬传会 TypeError；
    签名检查让两种形态并存，注入方无需感知版本差异。结果按 provider 对象缓存。
    """
    try:
        params = inspect.signature(provider).parameters
    except (TypeError, ValueError):  # 内置/奇异可调用对象，保守不透传
        return False
    return any(p.kind is inspect.Parameter.KEYWORD_ONLY and p.name == "trip_date"
               for p in params.values())


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """两点球面直线距离（km）。"""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def route_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """估算实际路程（km）：直线距离 × 绕行系数。"""
    return haversine_km(lat1, lng1, lat2, lng2) * T.TRAVEL_ROUTE_DETOUR_FACTOR


def choose_mode(distance_km: float) -> str:
    """路程超过步行上限则乘车。"""
    return "walk" if distance_km <= T.TRAVEL_WALK_MAX_KM else "drive"


def leg_minutes(distance_km: float, mode: str | None = None) -> int:
    """路程 → 通勤分钟数（向上取整，并保底最小耗时）。"""
    mode = mode or choose_mode(distance_km)
    speed = T.TRAVEL_WALK_KMH if mode == "walk" else T.TRAVEL_DRIVE_KMH
    if speed <= 0:
        return _MIN_MINUTES.get(mode, 8)
    minutes = math.ceil(distance_km / speed * 60)
    return max(_MIN_MINUTES.get(mode, 5), minutes)


def leg_cost_cny(distance_km: float, mode: str) -> float:
    """单段通勤费用（整车口径，不再乘人数）。

    步行 0 元；乘车 = 起步价 + 里程费。分摊问题不在此处处理 ——
    行程单展示的是「这一趟要花多少」，人均是用户自己的算术。
    """
    if mode == "walk":
        return 0.0
    return round(T.TRAVEL_TRANSIT_BASE_CNY + T.TRAVEL_TRANSIT_PER_KM_CNY * distance_km, 2)


def estimate_leg(
    from_lat: float, from_lng: float, to_lat: float, to_lng: float,
    *,
    trip_date: date | None = None,
) -> dict:
    """一次通勤的完整估算，返回可直接构造 TransitLeg 的字段。

    已注入真实路线数据源时优先使用其结果；数据源不可用或查不到时，
    回落到本地直线×绕行系数估算 —— 通勤估算失败不应让整个行程生成失败。

    trip_date：出行日期。仅当 provider 声明支持（kw-only ``trip_date``）时透传，
    供 providers/travel 执行「远期出行日期强制本地估算」策略；旧式 provider 不受影响。
    """
    if _route_provider is not None:
        try:
            if _accepts_trip_date(_route_provider):
                live = _route_provider(from_lat, from_lng, to_lat, to_lng,
                                       trip_date=trip_date)
            else:
                live = _route_provider(from_lat, from_lng, to_lat, to_lng)
        except Exception as e:  # noqa: BLE001 — 数据源异常不得影响排程主链路
            logger.warning("[TravelRouting] 真实路线数据源异常，回退本地估算: %s", e)
            live = None
        if live:
            return live

    distance = route_km(from_lat, from_lng, to_lat, to_lng)
    mode = choose_mode(distance)
    return {
        "distance_km": round(distance, 2),
        "mode": mode,
        "minutes": leg_minutes(distance, mode),
        "cost_cny": leg_cost_cny(distance, mode),
        # 显式标注来源，与 live_leg 的返回保持对称：调用方拿到的 dict 可以直接
        # 构造 TransitLeg，不必依赖模型默认值来补 provenance。
        "source": SOURCE_LOCAL,
        # 时效标注（Phase 1）：本地估算是「此刻生成的估算」，无实时路况成分
        "observed_at": _local_observed_at(),
        "traffic_aware": False,
        "is_estimate": True,
        "fallback_reason": None,
    }


def _local_observed_at() -> str:
    """本地估算的观测时刻（惰性导入，避免 facts → config 的加载环）。"""
    from backend.providers.travel.facts import now_iso

    return now_iso()


def day_radius_km(points: list[tuple[float, float]]) -> float:
    """一组坐标的最大两两路程（km）—— 度量「当日是否来回横跳」。

    用最大两两距离而非「包围盒对角线」：后者对三角形分布的三个点会高估。
    """
    if len(points) < 2:
        return 0.0
    worst = 0.0
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            worst = max(worst, route_km(points[i][0], points[i][1],
                                        points[j][0], points[j][1]))
    return round(worst, 2)
