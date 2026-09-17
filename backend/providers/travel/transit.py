"""providers/travel/transit.py — 通勤数据 Provider（Phase 1 §9）

把「通勤时长从哪来、可不可信」收敛为可替换的 Provider，并在 Provider 层
完成两条企业级策略：

1. **远期出行日期强制降级**：出行日期距今超过可信窗口（默认 14 天）时，
   当日实时路况对那天没有意义 —— 9 月排元旦行程套用 9 月的路况是隐性伪事实。
   此时**不发实时请求**，直接产出本地估算并标注
   ``fallback_reason="trip_date_beyond_horizon"``，reporter 会提示用户
   「临近出发请重新估算」。
2. **降级可解释**：实时请求失败回落本地估算时（现有 estimate_leg 语义），
   由 Provider 产出 ``fallback_reason``，让「为什么这一段是估算」可归因，
   而不是让用户对着 source 标签猜。

返回 dict 与 ``routing.estimate_leg`` 同构（可直接 ``**`` 展开构造 TransitLeg），
新增时效字段由 TransitLeg 的向后兼容默认值承接。
"""
from __future__ import annotations

from datetime import date
from typing import Protocol

from backend.config import travel as T
from backend.shared.logger import logger
from backend.tools.travel import routing

from backend.providers.travel.facts import (
    FAR_TRIP_FALLBACK_REASON,
    is_far_trip,
    now_iso,
)


class TransitProvider(Protocol):
    """通勤数据源契约。返回 dict 与 estimate_leg 同构；None 表示本段无数据。"""

    name: str

    def estimate(
        self,
        from_lat: float, from_lng: float, to_lat: float, to_lng: float,
        *,
        trip_date: date | None = None,
    ) -> dict | None:
        ...


class LocalEstimateProvider:
    """本地直线估算源（纯函数，永远可用，无外部依赖）。"""

    name = "estimate:local"

    def estimate(
        self,
        from_lat: float, from_lng: float, to_lat: float, to_lng: float,
        *,
        trip_date: date | None = None,
        fallback_reason: str | None = None,
    ) -> dict:
        distance = routing.route_km(from_lat, from_lng, to_lat, to_lng)
        mode = routing.choose_mode(distance)
        return {
            "distance_km": round(distance, 2),
            "mode": mode,
            "minutes": routing.leg_minutes(distance, mode),
            "cost_cny": routing.leg_cost_cny(distance, mode),
            "source": routing.SOURCE_LOCAL,
            "observed_at": now_iso(),
            "traffic_aware": False,
            "is_estimate": True,
            "fallback_reason": fallback_reason,
        }


class TencentTransitProvider:
    """腾讯真实路线源（live_leg 包装 + 远期降级策略 + 降级归因）。"""

    name = "tencent:lbs"

    def __init__(self, far_trip_days: int | None = None) -> None:
        from backend.tools.travel import live_map

        self._live_map = live_map
        self._local = LocalEstimateProvider()
        self._far_days = far_trip_days if far_trip_days is not None else T.TRAVEL_TRANSIT_FAR_TRIP_DAYS

    def is_enabled(self) -> bool:
        return self._live_map.is_enabled()

    def estimate(
        self,
        from_lat: float, from_lng: float, to_lat: float, to_lng: float,
        *,
        trip_date: date | None = None,
    ) -> dict | None:
        # 远期出行日期：实时路况对那天没有意义，直接本地估算并归因。
        # 注意不返回 None —— 返回 None 会让 estimate_leg 走无归因的回落，
        # 用户就看不到「为什么这一段不是实时数据」。
        if is_far_trip(trip_date, self._far_days):
            return self._local.estimate(
                from_lat, from_lng, to_lat, to_lng,
                fallback_reason=FAR_TRIP_FALLBACK_REASON,
            )

        try:
            live = self._live_map.live_leg(from_lat, from_lng, to_lat, to_lng)
        except Exception as e:  # noqa: BLE001 — 数据源异常不得影响排程主链路
            logger.warning("[TravelTransitProvider] 实时路线异常，回落本地估算: %s", e)
            live = None

        if live is None:
            # 网络失败/未启用 → 返回 None，由 estimate_leg 现有回落语义接管
            #（保持「数据源不需要自己实现降级」的旧契约）
            return None

        # 实时事实打标：来自真实路径与路况，非估算；键集与本地估算完全同构，
        # 下游（TransitLeg 构造、trace 归因）不需要 .get 防御。
        live["observed_at"] = now_iso()
        live["traffic_aware"] = True
        live["is_estimate"] = False
        live["fallback_reason"] = None
        return live


def build_transit_provider() -> TransitProvider | None:
    """按配置组装通勤 Provider：启用真实地图 → 腾讯源；否则 None（纯本地估算）。"""
    from backend.tools.travel import live_map

    if live_map.is_enabled():
        return TencentTransitProvider()
    return None
