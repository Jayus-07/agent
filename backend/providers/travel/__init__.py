"""providers.travel — 旅游域数据 Provider 层（Phase 1）

结构：
- facts    — 时效语义常量与工具（observed_at / verification_status / 远期判定）
- poi      — POIProvider：SeedPOIProvider（verified）/ TencentPOIProvider（unverified 标注）
- transit  — TransitProvider：TencentTransitProvider（远期降级 + 归因）/ LocalEstimateProvider

接线入口 ``install_travel_providers()``：在域注册（travel/register.py）时调用，
把 Provider 组装进 routing 的既有插槽（set_route_provider），保持
「未配置 Key 或开关关闭 → 本地估算」的既有降级语义不变。
"""
from __future__ import annotations

from backend.providers.travel.poi import (
    POIProvider,
    SeedPOIProvider,
    TencentPOIProvider,
)
from backend.providers.travel.transit import (
    LocalEstimateProvider,
    TransitProvider,
    TencentTransitProvider,
    build_transit_provider,
)
# 预订契约（任务书 §12，Phase 7 预留）：仅导出形状，未接线、无调用方
from backend.providers.travel.booking import (
    BookingProvider,
    BookingRecord,
    BookingRequest,
    BookingStatus,
    can_transition,
    make_idempotency_key,
)

__all__ = [
    "POIProvider",
    "SeedPOIProvider",
    "TencentPOIProvider",
    "TransitProvider",
    "LocalEstimateProvider",
    "TencentTransitProvider",
    "build_transit_provider",
    "install_travel_providers",
    "BookingProvider",
    "BookingRequest",
    "BookingRecord",
    "BookingStatus",
    "can_transition",
    "make_idempotency_key",
]


def install_travel_providers() -> dict[str, bool]:
    """按配置组装并接线旅游域数据 Provider（幂等，进程内只装一次语义）。

    Returns:
        {"transit_live": bool} — transit_live=True 表示实时路线源已接入。
        False 时 routing 保持本地直线估算（与旧 install_live_map 未启用分支语义一致）。

    POI 侧当前不改变调用方式：城市级候选仍走 SeedPOIProvider（经 tools/travel/poi），
    点名补全仍走 live_map.resolve_missing_places，但补全产物经
    TencentPOIProvider 打 unverified 标（见 poi_expert 侧的接线说明）。
    """
    from backend.tools.travel import routing

    provider = build_transit_provider()
    if provider is None:
        routing.set_route_provider(None)
        return {"transit_live": False}

    routing.set_route_provider(provider.estimate)
    return {"transit_live": True}
