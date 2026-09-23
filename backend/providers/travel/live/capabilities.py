"""providers/travel/live/capabilities.py — Provider 能力账（STOP J1 §19）

「哪个 Provider 支持什么」的单一事实源。业务选择 Provider 前先查这里，
不得假设能力存在（如「腾讯应该有营业时间」——J0 审计证明没有）。

hotel.search / flight.search 为 OUT（HOTEL_PROVIDER_SCOPE=OUT /
FLIGHT_PROVIDER_SCOPE=OUT，STOP J0 §104 冻结；STOP K 再评估）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderCapability:
    """一条能力声明（capability → 支持状态与提供方）。"""

    capability: str
    implemented: bool
    provider: str | None      # 当前实现方；None=无实现方
    note: str = ""


_PROVIDER_CAPABILITIES: tuple[ProviderCapability, ...] = (
    ProviderCapability("maps.place_resolve", True, "tencent:lbs",
                       "must_go 点名解析；坐标/名称权威，营业时间票价不提供"),
    ProviderCapability("maps.route", True, "tencent:lbs",
                       "driving/walking 路线+实时路况+taxi_fare"),
    ProviderCapability("weather.forecast", True, "tencent:lbs",
                       "now/future；future 视野以 Provider 实际返回为准"),
    ProviderCapability("ticket.facts", False, None,
                       "腾讯 WebService 无票价/营业时间字段（J0-4 实测）；"
                       "契约已冻结（contracts.TicketProvider），无真实适配器——"
                       "继续 unknown/unverified，不硬凑"),
    ProviderCapability("hotel.search", False, None,
                       "HOTEL_PROVIDER_SCOPE=OUT（无消费链，STOP K）"),
    ProviderCapability("flight.search", False, None, "FLIGHT_PROVIDER_SCOPE=OUT"),
)


def get_capability(capability: str) -> ProviderCapability | None:
    for cap in _PROVIDER_CAPABILITIES:
        if cap.capability == capability:
            return cap
    return None


def all_capabilities() -> tuple[ProviderCapability, ...]:
    return _PROVIDER_CAPABILITIES
