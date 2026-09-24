"""travel/commerce/request.py — 搜索请求与确定性校验（STOP K1，任务书 §七/§八）

**全部校验确定性**（G11/G12）：晚数 = check_out - check_in 由代码计算，
绝不让模型算；日期非法/过去日期/退房≤入住/rooms<1/origin==destination
一律 ValueError（可读信息直达澄清话术），不猜不修。

机场/城市代码来源：Provider 或 verified static mapping（cities.py 的静态
城市表）——LLM 不得造 IATA code（§八）。
"""
from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# 支持的最大单次入住晚数（防 9999 晚这类DoS/手滑；超过即拒绝而非截断）
_MAX_NIGHTS = 30
_MAX_TRIP_DAYS = 30


class HotelSearchRequest(BaseModel):
    """酒店搜索请求（§七最低输入集 + 可选过滤）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    city: str = Field(min_length=1)
    check_in: date
    check_out: date
    adults: int = Field(default=2, ge=1)
    children: int = Field(default=0, ge=0)
    rooms: int = Field(default=1, ge=1)
    star_rating: int | None = Field(default=None, ge=1, le=5)
    hotel_name: str | None = None

    @field_validator("city")
    @classmethod
    def _city_not_blank(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("城市不能为空")
        return v

    @model_validator(mode="after")
    def _dates_valid(self) -> "HotelSearchRequest":
        today = date.today()
        if self.check_in < today:
            raise ValueError(f"入住日期 {self.check_in} 不能早于今天 {today}")
        if self.check_out <= self.check_in:
            raise ValueError(
                f"退房日期 {self.check_out} 必须晚于入住日期 {self.check_in}")
        if (self.check_out - self.check_in).days > _MAX_NIGHTS:
            raise ValueError(f"单次入住晚数不能超过 {_MAX_NIGHTS} 晚")
        if (self.check_in - today).days > 365:
            raise ValueError("入住日期过早（一年以上），供应商通常不开放查询")
        return self

    @property
    def nights(self) -> int:
        """入住晚数（确定性计算唯一出口）。"""
        return (self.check_out - self.check_in).days


class FlightSearchRequest(BaseModel):
    """机票搜索请求（§八最低输入集；one-way 必持，round-trip 视 Provider）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    origin: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    departure_date: date
    return_date: date | None = None
    adults: int = Field(default=1, ge=1)
    children: int = Field(default=0, ge=0)
    cabin: str | None = None

    @field_validator("origin", "destination")
    @classmethod
    def _city_not_blank(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("出发地/目的地不能为空")
        return v

    @model_validator(mode="after")
    def _dates_valid(self) -> "FlightSearchRequest":
        today = date.today()
        if self.origin.strip().upper() == self.destination.strip().upper():
            raise ValueError("出发地与目的地不得相同")
        if self.departure_date < today:
            raise ValueError(
                f"出发日期 {self.departure_date} 不能早于今天 {today}")
        if self.return_date is not None:
            if self.return_date <= self.departure_date:
                raise ValueError(
                    f"返程日期 {self.return_date} 必须晚于出发日期 "
                    f"{self.departure_date}")
        if (self.departure_date - today).days > 365:
            raise ValueError("出发日期过早（一年以上），供应商通常不开放查询")
        return self

    @property
    def round_trip(self) -> bool:
        return self.return_date is not None

    @property
    def trip_days(self) -> int | None:
        if self.return_date is None:
            return None
        return (self.return_date - self.departure_date).days


def horizon_limit() -> date:
    """可查询的最远日期（配置口径，供澄清话术引用）。"""
    from backend.config.travel_commerce import TRAVEL_COMMERCE_DATE_HORIZON_DAYS

    return date.today() + timedelta(days=TRAVEL_COMMERCE_DATE_HORIZON_DAYS)
