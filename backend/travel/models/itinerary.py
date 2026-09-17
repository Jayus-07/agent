"""travel/models/itinerary.py — 行程契约（输出主契约）

Itinerary 是旅游域的**输出契约**，与 SQLResult / BusinessInsight 同级：
  - 由专家逐层填充（poi 定骨架 → transit 定时刻 → budget 定费用）
  - 由 validator 校验
  - 由 reporter 渲染成给用户看的行程单
  - 全字段可 JSON 序列化，可落库、可作为 trace 附件

时间一律用 "HH:MM" 字符串：跨天不会溢出，序列化稳定，人可读。
需要算数的地方统一走 minutes 派生字段，避免各处重复解析。
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from backend.travel.models.brief import TravelBrief
from backend.travel.models.poi import Poi

KIND_VISIT = "visit"
KIND_MEAL = "meal"
KIND_REST = "rest"


class ItineraryItem(BaseModel):
    """行程中的一项安排

    poi 为 None 表示非 POI 项目（用餐、休整）—— 这些同样占用时间，
    必须进入时间轴，否则排出来的时刻是假的。
    """

    title: str = Field(..., description="展示标题")
    kind: str = Field(default=KIND_VISIT, description="visit / meal / rest")
    start: str = Field(..., description="开始时刻 HH:MM")
    end: str = Field(..., description="结束时刻 HH:MM")
    minutes: int = Field(..., description="时长（分钟）")
    wait_minutes: int = Field(
        default=0, description="到达后等待开放的时间（分钟），0 表示无需等待"
    )
    poi: Poi | None = Field(default=None, description="关联 POI，用餐/休整为 None")
    note: str = Field(default="", description="备注（如闭馆提示、预约提醒）")


class TransitLeg(BaseModel):
    """相邻两项之间的通勤段"""

    from_title: str = Field(..., description="起点标题")
    to_title: str = Field(..., description="终点标题")
    minutes: int = Field(..., description="通勤分钟数")
    distance_km: float = Field(default=0.0, description="估算路程（km）")
    mode: str = Field(default="drive", description="walk / drive")
    cost_cny: float = Field(default=0.0, description="通勤费用（整车口径）")
    # 溯源：通勤时长是可证伪的事实。estimate:local 为直线×绕行系数估算，
    # tencent:lbs 为腾讯位置服务真实路径规划结果 —— 行程单应如实标注，
    # 避免把估算值当导航结果呈现给用户。
    source: str = Field(default="estimate:local", description="通勤数据来源")
    # —— 时效语义（Phase 1，任务书 §9）：实时路况有观测时刻与适用窗口 ——
    # 远期出行日期强制本地估算并给 fallback_reason；未标注时按估算处理（保守）。
    observed_at: str | None = Field(
        default=None, description="路况观测时间（ISO 8601 UTC）；本地估算为生成时刻"
    )
    traffic_aware: bool = Field(
        default=False, description="时长是否来自真实路况（tencent:lbs 实时路径）"
    )
    is_estimate: bool = Field(
        default=True, description="数值是否为估算；未标注按估算处理（保守披露）"
    )
    fallback_reason: str | None = Field(
        default=None,
        description="降级原因（如 trip_date_beyond_horizon=出行日期超出实时数据可信窗口）；无降级为 None",
    )


class ItineraryDay(BaseModel):
    """单日行程"""

    # 字段名不可用 date —— 会遮蔽上方 import 的 date 类型，Pydantic 解析
    # 注解时会解析失败（实测 TypeError: Unable to evaluate 'date | None'）
    day_index: int = Field(..., ge=1, description="第几天（1-based）")
    day_date: date | None = Field(
        default=None, description="具体日期；无 start_date 时为 None"
    )
    items: list[ItineraryItem] = Field(default_factory=list)
    legs: list[TransitLeg] = Field(default_factory=list)
    active_minutes: int = Field(default=0, description="有效活动时长（不含通勤）")
    transit_minutes: int = Field(default=0, description="当日通勤总时长")
    cost_cny: float = Field(default=0.0, description="当日预估花费")

    def visit_items(self) -> list[ItineraryItem]:
        return [i for i in self.items if i.kind == KIND_VISIT]


class CostBreakdown(BaseModel):
    """费用拆分 —— 让「超预算」可解释，而不只是一个超标数字"""

    tickets: float = 0.0
    meals: float = 0.0
    lodging: float = 0.0
    transit: float = 0.0

    @property
    def total(self) -> float:
        return round(self.tickets + self.meals + self.lodging + self.transit, 2)


class Itinerary(BaseModel):
    """完整行程（输出契约）

    warnings / sources / confidence 三个字段是「可信交付」的载体：
      warnings:   未接入的数据源、被迫做的取舍、提示级约束违反
      sources:    行程所依据的数据来源（营业时间/票价的出处）
      confidence: 0-1，反映「数据完备度 × 校验通过度」，不是模型自评
    """

    brief: TravelBrief = Field(..., description="生成本行程所依据的需求")
    days: list[ItineraryDay] = Field(default_factory=list)
    cost: CostBreakdown = Field(default_factory=CostBreakdown)
    sources: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    repair_rounds: int = Field(default=0, description="局部修复轮数，>0 表示首版未通过校验")

    def total_pois(self) -> int:
        return sum(len(d.visit_items()) for d in self.days)

    def all_pois(self) -> list[Poi]:
        return [i.poi for d in self.days for i in d.items if i.poi is not None]
