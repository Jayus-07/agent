"""providers/travel/live/commerce_contracts.py — Hotel/Flight Provider 契约（STOP K1）

**本文件是 STOP K 新增，不改 frozen contracts.py 的任何内容**（K0 §4：
CAP_HOTEL_SEARCH/CAP_FLIGHT_SEARCH 常量继续从原文件 import——常量即登记位）。

与 STOP J 四契约同构（K0 §5 复用清单）：业务层依赖 Contract，Provider 层依赖
SDK/API。归一化 Record 是 **JSON-safe dataclass**（腾讯式原始 schema 不得越过
本层）；**严格校验的 Pydantic 模型在 travel/commerce/models.py（业务侧）**——
Record → 模型的转换即 fail-closed 校验门：转换失败 = INVALID_RESPONSE，
脏数据绝不穿透（G10）。

红线（任务书 §二）：
- Record 字段 Provider 没给就是 None——**禁止模型/适配器补齐**；
- availability 是枚举字符串而非 bool（unknown != available，G5）；
- 金额缺失/非法在业务侧校验门拒绝，本层不猜不补；
- booking_deep_link=None 表示 Provider 未提供——禁止拼假地址（§12）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable

from backend.providers.travel.live.result import ProviderResult

# availability 枚举值（业务侧 models.Availability 的字符串形态；
# 以字符串过层，非法值在业务校验门拒绝）
AVAIL_AVAILABLE = "available"
AVAIL_LIMITED = "limited"
AVAIL_SOLD_OUT = "sold_out"
AVAIL_UNKNOWN = "unknown"
AVAILABILITY_VALUES = frozenset(
    {AVAIL_AVAILABLE, AVAIL_LIMITED, AVAIL_SOLD_OUT, AVAIL_UNKNOWN})

# tax_inclusion 枚举值（unknown != included != excluded）
TAX_UNKNOWN = "unknown"
TAX_INCLUDED = "included"
TAX_EXCLUDED = "excluded"
TAX_INCLUSION_VALUES = frozenset({TAX_UNKNOWN, TAX_INCLUDED, TAX_EXCLUDED})


# =============================================
# 归一化数据形状（JSON-safe dataclass）
# =============================================


@dataclass
class PriceSnapshotRecord:
    """价格快照（Provider 侧原始形状）。

    amount 为字符串或 Decimal（由适配器保证非 float——float 做钱是红线 G4）；
    taxes/fees=None 表示 **unknown**（禁止 0 充当）；expires_at 仅在 Provider
    明示价格有效期时给出，禁止自造 Provider guarantee（§K5）。
    """

    amount: str
    currency: str
    base_amount: str | None = None
    taxes: str | None = None
    fees: str | None = None
    tax_inclusion: str = TAX_UNKNOWN
    expires_at: str | None = None
    provider_offer_id: str | None = None


@dataclass
class HotelOfferRecord:
    """酒店报价（字段 Provider 没给 = None，适配器不得补齐）。"""

    provider: str
    property_id: str
    property_name: str
    city: str
    check_in: str                     # ISO 日期 YYYY-MM-DD
    check_out: str
    availability: str                 # AVAILABILITY_VALUES 之一
    price: PriceSnapshotRecord
    provider_offer_id: str | None = None    # rate plan 标识（指纹要素，缺失记 None）
    address: str | None = None
    lat: float | None = None          # 仅 Provider 有真实坐标时非 None
    lng: float | None = None
    room_type: str | None = None
    adults: int = 2
    children: int = 0
    rooms: int = 1
    cancellation_policy: str | None = None  # None=未知，禁输出「不可退款」
    meal_plan: str | None = None
    booking_deep_link: str | None = None
    source_id: str | None = None
    extra: dict = field(default_factory=dict)  # 供 fake/live 适配器挂测试标记


@dataclass
class FlightSegmentRecord:
    """单航段（carrier/flight_number/机场码/时刻全部来自 Provider）。"""

    carrier: str
    flight_number: str
    origin_airport: str
    destination_airport: str
    departure_at: str                 # ISO datetime（含时区偏移）
    arrival_at: str


@dataclass
class FlightOfferRecord:
    """机票报价。机场/城市代码必须来自 Provider 或 verified static mapping
    （K0 §7），适配器不得让 LLM 造 IATA code。"""

    provider: str
    origin: str
    destination: str
    segments: list[FlightSegmentRecord]
    availability: str
    price: PriceSnapshotRecord
    provider_offer_id: str | None = None
    duration_minutes: int | None = None
    stops: int | None = None          # None → 业务层按 segments 数确定性推导
    cabin: str | None = None
    baggage: str | None = None
    fare_rules: str | None = None
    booking_deep_link: str | None = None
    source_id: str | None = None
    extra: dict = field(default_factory=dict)


# =============================================
# Provider 契约（Protocol；与 PlaceProvider/RoutingProvider 同风格，
# 原始类型入参——契约不依赖业务 request 模型，保持 Provider 层无业务依赖）
# =============================================


@runtime_checkable
class HotelSearchProvider(Protocol):
    """酒店搜索：city 为城市名（Provider 侧自行解析），日期为 ISO date。"""

    name: str

    def search_hotels(
        self, city: str, check_in: date, check_out: date, *,
        adults: int = 2, children: int = 0, rooms: int = 1,
        star_rating: int | None = None,
    ) -> ProviderResult[list[HotelOfferRecord]]:
        """无结果 → SUCCESS+[] 或 NOT_FOUND；失败 → 七态之一。禁止编造。"""
        ...


@runtime_checkable
class FlightSearchProvider(Protocol):
    """机票搜索：单程必持；往返仅在 Provider 支持时给 return_date。"""

    name: str

    def search_flights(
        self, origin: str, destination: str, departure_date: date, *,
        return_date: date | None = None, adults: int = 1, children: int = 0,
        cabin: str | None = None,
    ) -> ProviderResult[list[FlightOfferRecord]]:
        ...
