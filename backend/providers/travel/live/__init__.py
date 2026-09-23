"""providers/travel/live — Provider Layer（STOP J1-J8）

外部事实进入冻结 Travel 管线的唯一通道：Contract → Validation → Cache →
Resilience → Quota → Normalization → 现有确定性规划管线。

- result/errors/contracts/capabilities —— 契约与分类学（J1）
- resilience/cache/quota —— 韧性/共享缓存/软预算（J2/J3/J7）
- tencent —— 腾讯 LBS 适配器（Place/Route/Weather；Ticket 无真实适配器，
  J0-6 决策 B：继续 unknown/unverified）
- health —— travel_providers health 组件（§98）

业务 experts 消费本层；``infra/http/tencent_lbs.py`` 与 ``infra/lbs/api.py``
作为传输层被复用（maps 路由等其他消费方行为不变）。
"""
from __future__ import annotations

from backend.providers.travel.live.capabilities import (
    all_capabilities,
    get_capability,
)
from backend.providers.travel.live.errors import ProviderError, status_from_lbs_error
from backend.providers.travel.live.result import (
    Freshness,
    ProviderResult,
    ProviderStatus,
    failure,
    success,
)

__all__ = [
    "Freshness",
    "ProviderResult",
    "ProviderStatus",
    "ProviderError",
    "status_from_lbs_error",
    "failure",
    "success",
    "all_capabilities",
    "get_capability",
    "get_place_provider",
    "get_routing_provider",
    "get_weather_provider",
]

_SINGLETONS: dict[str, object] = {}


def get_place_provider():
    """PlaceProvider 单例（TencentPlaceProvider）。"""
    if "place" not in _SINGLETONS:
        from backend.providers.travel.live.tencent import TencentPlaceProvider

        _SINGLETONS["place"] = TencentPlaceProvider()
    return _SINGLETONS["place"]


def get_routing_provider():
    if "routing" not in _SINGLETONS:
        from backend.providers.travel.live.tencent import TencentRoutingProvider

        _SINGLETONS["routing"] = TencentRoutingProvider()
    return _SINGLETONS["routing"]


def get_weather_provider():
    if "weather" not in _SINGLETONS:
        from backend.providers.travel.live.tencent import TencentWeatherProvider

        _SINGLETONS["weather"] = TencentWeatherProvider()
    return _SINGLETONS["weather"]
