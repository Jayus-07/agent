"""travel/commerce/models.py — Commerce 领域契约（STOP K1，任务书 §六）

**Money 禁止 float（G4）**：Decimal 贯穿全链；JSON 序列化（model_dump
mode="json"）落字符串，LangGraph state / SSE 均无精度损耗。

**unknown 语义红线（G5/G8）**：
  taxes/fees = None  → unknown（禁止 0 充当；「含税 ¥0」由此被结构性排除）
  cancellation_policy = None → 未知（禁止输出「不可退款」）
  availability = UNKNOWN → Provider 未给库存（禁止输出「有房」）
  expires_at = None → Provider 未承诺价格有效期（禁止伪造 guarantee）；
    系统 cache TTL 是另一回事（我们多久重查），两者绝不混写（§K5）。

**fail-closed（G10）**：全部模型 extra="forbid"——Provider 侧 Record 转换时
多字段/少字段/非法值一律抛 ValidationError，由服务层映射 INVALID_RESPONSE，
脏数据不穿透到渲染。

本层字段命名面向业务消费（reporter/API），与 Provider 侧
commerce_contracts.Record 一一对应；Record → 模型的转换在 normalize.py。
"""
from __future__ import annotations

from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.providers.travel.live.commerce_contracts import (
    TAX_EXCLUDED,
    TAX_INCLUDED,
    TAX_UNKNOWN,
)
from backend.providers.travel.live.result import Freshness


class Availability(str, Enum):
    """可订状态（任务书 §六：明确状态而非 bool）。

    特别地：**Provider 失败不可能映射为 SOLD_OUT**——非 SUCCESS 结局根本
    不产生 offer 对象（结构性保证，测试钉死）。
    """

    AVAILABLE = "available"      # Provider 明确可订
    LIMITED = "limited"          # Provider 明确少量（如「仅剩 X 间/座」）
    SOLD_OUT = "sold_out"        # Provider 明确售罄
    UNKNOWN = "unknown"          # Provider 未给库存信息——绝不当 available


class TaxInclusion(str, Enum):
    """税费口径（Provider 声明；未声明 = unknown）。"""

    UNKNOWN = TAX_UNKNOWN
    INCLUDED = TAX_INCLUDED
    EXCLUDED = TAX_EXCLUDED


_MODEL_CFG = ConfigDict(frozen=True, extra="forbid")


class Money(BaseModel):
    """金额（Decimal + ISO 币种；Provider 未给 currency 不得猜）。"""

    model_config = _MODEL_CFG

    amount: Decimal
    currency: str

    @field_validator("amount")
    @classmethod
    def _amount_finite_non_negative(cls, v: Decimal) -> Decimal:
        if not v.is_finite():
            raise ValueError(f"金额必须为有限数，得到 {v}")
        if v < 0:
            raise ValueError(f"金额不能为负，得到 {v}")
        return v

    @field_validator("currency")
    @classmethod
    def _currency_iso(cls, v: str) -> str:
        v = (v or "").strip().upper()
        if len(v) != 3 or not v.isalpha() or not v.isascii():
            raise ValueError(f"currency 必须为 3 位 ISO 4217 码，得到 {v!r}")
        return v

    def render(self) -> str:
        """面向用户的金额渲染（Decimal → 字符串，无 float 参与精度无损）。"""
        amount = self.amount
        if amount == amount.to_integral_value():
            return f"{amount:.0f} {self.currency}"
        return f"{amount.normalize()} {self.currency}"


class PriceSnapshot(BaseModel):
    """价格快照（任务书 §六/§K5：价格是有时间语义的数据，不是普通字段）。"""

    model_config = _MODEL_CFG

    snapshot_id: str                  # fingerprint+observed_at 派生（identity.py）
    provider: str
    provider_offer_id: str | None = None
    amount: Money                     # total（>0；0 元报价非法，校验门拒绝）
    base_amount: Money | None = None  # Provider 只给 total 时 = None（unknown）
    taxes: Money | None = None        # None = unknown，禁 0 充当（G8）
    fees: Money | None = None
    tax_inclusion: TaxInclusion = TaxInclusion.UNKNOWN
    observed_at: str                  # ISO 时间：价格观测时点（必填）
    expires_at: str | None = None     # 仅 Provider 明示时非 None
    freshness: Freshness = Freshness.LIVE
    source_id: str | None = None      # Provider 侧溯源 id

    @field_validator("amount")
    @classmethod
    def _total_positive(cls, v: Money) -> Money:
        if v.amount <= 0:
            raise ValueError(f"报价总额必须 > 0，得到 {v.amount}")
        return v


class Occupancy(BaseModel):
    """入住人数/房间数（指纹要素）。"""

    model_config = _MODEL_CFG

    adults: int = Field(default=2, ge=1)
    children: int = Field(default=0, ge=0)
    rooms: int = Field(default=1, ge=1)


class AvailabilityObservation(BaseModel):
    """一次可订状态观测（状态 + 观测时间——库存同样有稍纵即逝的时效）。"""

    model_config = _MODEL_CFG

    status: Availability
    observed_at: str


class HotelOffer(BaseModel):
    """酒店报价（任务书 §六最低字段集；字段不存在 = None，禁止补齐）。"""

    model_config = _MODEL_CFG

    provider: str
    provider_offer_id: str | None = None
    property_id: str
    property_name: str
    city: str
    address: str | None = None
    lat: float | None = None
    lng: float | None = None

    check_in: str                     # YYYY-MM-DD
    check_out: str
    nights: int = Field(ge=1)         # 服务层确定性计算（check_out - check_in）
    room_type: str | None = None
    occupancy: Occupancy

    availability: AvailabilityObservation
    price_snapshot: PriceSnapshot

    cancellation_policy: str | None = None
    meal_plan: str | None = None

    booking_deep_link: str | None = None    # None = 不可提供，禁拼假地址

    observed_at: str
    freshness: Freshness = Freshness.LIVE
    source_id: str | None = None
    offer_fingerprint: str

    @model_validator(mode="after")
    def _dates_consistent(self) -> "HotelOffer":
        from datetime import date as _date

        try:
            ci = _date.fromisoformat(self.check_in)
            co = _date.fromisoformat(self.check_out)
        except ValueError as e:
            raise ValueError(f"入住/退房日期非法：{e}") from None
        if co <= ci:
            raise ValueError("check_out 必须晚于 check_in")
        if self.nights != (co - ci).days:
            raise ValueError(
                f"nights={self.nights} 与日期区间 {(co - ci).days} 不一致")
        return self


class FlightSegment(BaseModel):
    """航段（carrier/航班号/机场/时刻全部来自 Provider，缺一不可）。"""

    model_config = _MODEL_CFG

    carrier: str
    flight_number: str
    origin_airport: str
    destination_airport: str
    departure_at: str                 # ISO datetime
    arrival_at: str

    @model_validator(mode="after")
    def _times_valid(self) -> "FlightSegment":
        from datetime import datetime as _dt

        fmt_error = ValueError(
            f"航段时刻非法：{self.departure_at} → {self.arrival_at}")
        try:
            dep = _dt.fromisoformat(self.departure_at)
            arr = _dt.fromisoformat(self.arrival_at)
        except ValueError:
            raise fmt_error from None
        if arr < dep:
            raise ValueError(
                f"到达时刻早于出发时刻：{self.departure_at} → {self.arrival_at}")
        if not self.carrier.strip() or not self.flight_number.strip():
            raise ValueError("carrier/flight_number 不得为空")
        return self


class FlightOffer(BaseModel):
    """机票报价（任务书 §六；不得根据航班号猜航空公司之外的动态事实）。"""

    model_config = _MODEL_CFG

    provider: str
    provider_offer_id: str | None = None
    origin: str
    destination: str

    segments: list[FlightSegment] = Field(min_length=1)
    duration_minutes: int | None = None     # None = Provider 未给（unknown）
    stops: int = Field(ge=0)                # 服务层按 segments 确定性推导
    cabin: str | None = None

    availability: AvailabilityObservation
    price_snapshot: PriceSnapshot

    baggage: str | None = None
    fare_rules: str | None = None

    booking_deep_link: str | None = None

    observed_at: str
    freshness: Freshness = Freshness.LIVE
    source_id: str | None = None
    offer_fingerprint: str

    @model_validator(mode="after")
    def _route_consistent(self) -> "FlightOffer":
        if self.origin.strip().upper() == self.destination.strip().upper():
            raise ValueError("origin 与 destination 不得相同")
        first = self.segments[0]
        last = self.segments[-1]
        if first.origin_airport.strip().upper() != self.origin.strip().upper():
            raise ValueError(
                f"首段出发机场 {first.origin_airport} 与 origin {self.origin} 不一致")
        if last.destination_airport.strip().upper() != self.destination.strip().upper():
            raise ValueError(
                f"末段到达机场 {last.destination_airport} 与 destination "
                f"{self.destination} 不一致")
        for a, b in zip(self.segments, self.segments[1:]):
            if a.destination_airport.strip().upper() != b.origin_airport.strip().upper():
                raise ValueError(
                    f"航段不衔接：{a.flight_number} 到达 "
                    f"{a.destination_airport}，下一段 {b.flight_number} 从 "
                    f"{b.origin_airport} 出发")
        return self
