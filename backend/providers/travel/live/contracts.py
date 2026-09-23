"""providers/travel/live/contracts.py — Provider 契约与能力注册表（STOP J1）

任务书 §15/§19：业务层依赖 Contract，Provider 层依赖 SDK/API；业务不得假设
「一个 Provider 什么都有」——能力差异走 :data:`PROVIDER_CAPABILITIES` 声明。

四个第一阶段契约（§104 Scope Gate）：
  PlaceProvider   maps.place_resolve（must_go 点名解析）
  RoutingProvider maps.route（段间路线）
  WeatherProvider weather.forecast（预报）
  TicketProvider  ticket.facts（票价/营业时间——J0-6 决策：契约冻结，
                  **无真实适配器**，腾讯 WebService 不提供该字段）

hotel.search / flight.search 登记为 not_implemented（HOTEL/FLIGHT SCOPE=OUT）。

既有 ``providers/travel/poi.py::POIProvider`` 与 ``transit.py::TransitProvider``
是 STOP I 冻结的**业务消费面**（返回 Poi/同构 dict）——本层契约是它们下面的
数据通道契约，返回 ProviderResult；两者不互相替代。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from backend.providers.travel.live.result import ProviderResult

# =============================================
# 归一化数据形状（JSON-safe，腾讯原始 schema 不得出本层）
# =============================================


@dataclass
class PlaceRecord:
    """地点解析结果（坐标与名称是权威事实；营业时间/票价不在本记录——
    该字段腾讯不提供，由 TicketProvider 契约承载 unknown 语义）。"""

    provider_id: str
    name: str
    category: str = ""
    address: str = ""
    province: str = ""
    city: str = ""
    district: str = ""
    adcode: str = ""
    lat: float = 0.0
    lng: float = 0.0
    telephone: str = ""


@dataclass
class RouteRecord:
    """一段路线（§40：distance/duration/mode + 溯源；不伪造实时）。"""

    distance_m: int
    duration_min: float
    mode: str
    taxi_fare_cny: float | None = None
    # 溯源：live=真实路径+路况；estimate 本地估算。traffic_aware 仅在
    # provider 返回真实路况时为 True（§41：不含实时交通不得写「实时车程」）
    traffic_aware: bool = False


@dataclass
class WeatherDay:
    """单日预报（字段以腾讯实际返回为准；缺失字段为 None 而非伪造）。"""

    date: str
    condition_day: str = ""
    condition_night: str = ""
    min_temp: float | None = None
    max_temp: float | None = None


@dataclass
class WeatherForecast:
    """未来几天预报（horizon_days 供 OUT_OF_HORIZON 判定，§43）。"""

    city: str
    days: list[WeatherDay]
    horizon_days: int
    forecast_generated_at: str = ""


@dataclass
class TicketFacts:
    """票价/营业事实（§46-§48）：

    ticket_price_cny=None 表示**未知**（Provider 没返回）——绝不允许用
    0 充当 unknown；明确免费必须是 price=0 且 verified=True。
    """

    provider_id: str
    ticket_price_cny: float | None
    currency: str = "CNY"
    opening_time: str | None = None
    close_time: str | None = None
    closed_weekdays: tuple[int, ...] = ()
    reservation_required: bool | None = None
    verified: bool = False


# =============================================
# Provider 契约（Protocol）
# =============================================


@runtime_checkable
class PlaceProvider(Protocol):
    """地点解析：把用户点名的地点名解析为权威坐标与名称。"""

    name: str

    def resolve_place(self, name: str, city: str) -> ProviderResult[PlaceRecord]:
        """解析不到 → NOT_FOUND；超时/挂 → TIMEOUT/UNAVAILABLE。禁止编造。"""
        ...


@runtime_checkable
class RoutingProvider(Protocol):
    """路线：两坐标点之间的真实路线；失败由调用方落 Haversine 估算。"""

    name: str

    def route(
        self, from_lat: float, from_lng: float, to_lat: float, to_lng: float,
        *, mode: str = "driving", trip_date: date | None = None,
    ) -> ProviderResult[RouteRecord]:
        ...


@runtime_checkable
class WeatherProvider(Protocol):
    """天气预报（未来几天）；只报 Provider 实际覆盖的 horizon。"""

    name: str

    def forecast(self, city: str) -> ProviderResult[WeatherForecast]:
        ...


@runtime_checkable
class TicketProvider(Protocol):
    """票价/营业事实。未知字段必须 None，禁止 0 充当 unknown（§47）。"""

    name: str

    def get_place_facts(self, provider_id: str) -> ProviderResult[TicketFacts]:
        ...


# =============================================
# 能力注册表（§19：业务不得假设一个 Provider 什么都有）
# =============================================
# capability 命名：<域>.<动作>；本注册表是 Provider 能力账，
# 与 Planner 的 capability DAG（capabilities.yaml）是两回事。
CAP_PLACE_RESOLVE = "maps.place_resolve"
CAP_ROUTE = "maps.route"
CAP_WEATHER_FORECAST = "weather.forecast"
CAP_TICKET_FACTS = "ticket.facts"
CAP_HOTEL_SEARCH = "hotel.search"      # OUT（STOP K）
CAP_FLIGHT_SEARCH = "flight.search"    # OUT（STOP K）
