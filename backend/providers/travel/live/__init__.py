"""providers/travel/live — Provider Layer（STOP J1-J8）

外部事实进入冻结 Travel 管线的唯一通道：Contract → Validation → Cache →
Resilience → Quota → Normalization → 现有确定性规划管线。

- result/errors/contracts/capabilities —— 契约与分类学（J1）
- resilience/cache/quota —— 韧性/共享缓存/软预算（J2/J3/J7）
- tencent —— 腾讯 LBS 适配器（Place/Route/Weather；Ticket 无真实适配器，
  J0-6 决策 B：继续 unknown/unverified）
- qweather —— 和风天气适配器（天气**备用源**，2026-09-28；配置
  QWEATHER_API_KEY 才参与，get_weather_provider 届时返回主备组合器）
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
    "get_qweather_provider",
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


class FallbackWeatherProvider:
    """天气主备组合器：主源（腾讯）失败才降级到备用源（和风）。

    两源返回同一冻结 future dict，weather 专家零感知；fallback 不跨语义——
    双源皆败时保留**主源**结局（主因优先，不把备用源的失败原因冒充主因）。
    DISABLED 不触发降级：总闸关闭是配置选择，不是故障。
    """

    name = "weather:primary-backup"
    operation = "weather"

    def __init__(self, primary, backup) -> None:
        self._primary = primary
        self._backup = backup

    def is_enabled(self) -> bool:
        return bool(self._primary.is_enabled() or self._backup.is_enabled())

    def forecast_payload(self, city: str) -> ProviderResult[dict]:
        from backend.providers.travel.live import telemetry
        from backend.providers.travel.live.result import ProviderStatus

        if not self._primary.is_enabled():
            return self._backup.forecast_payload(city)
        primary_result = self._primary.forecast_payload(city)
        if (primary_result.ok
                or primary_result.status == ProviderStatus.DISABLED
                or not self._backup.is_enabled()):
            return primary_result
        backup_result = self._backup.forecast_payload(city)
        if backup_result.ok:
            telemetry.record_fallback(self._backup.name, "backup_source")
            telemetry.event("travel.provider.fallback", provider=self._backup.name,
                            fallback="backup_source",
                            underlying=primary_result.status.value)
            return backup_result
        return primary_result


def get_qweather_provider():
    """QWeatherProvider 单例（和风天气，备用源）。"""
    if "qweather" not in _SINGLETONS:
        from backend.providers.travel.live.qweather import QWeatherProvider

        _SINGLETONS["qweather"] = QWeatherProvider()
    return _SINGLETONS["qweather"]


def get_weather_provider():
    if "weather" not in _SINGLETONS:
        from backend.config.map import is_qweather_configured
        from backend.providers.travel.live.tencent import TencentWeatherProvider

        primary = TencentWeatherProvider()
        if is_qweather_configured():
            _SINGLETONS["weather"] = FallbackWeatherProvider(
                primary, get_qweather_provider())
        else:
            _SINGLETONS["weather"] = primary
    return _SINGLETONS["weather"]
