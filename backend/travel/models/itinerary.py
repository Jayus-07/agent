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

# ============================================================
# plan 状态机与版本链常量（任务书 §4）
# ============================================================
# 状态流转：validating（排程/重排出生时）→ ready / degraded / needs_user_decision
# （validator 判定）。needs_user_decision = 无 error 级违反、但存在必去项冲突
# 等需用户取舍的约束（任务书 §7 USER_DECISION 层级）——行程可交付但选项要摆明。
# failed 时无行程可挂，不落此字段。
PLAN_STATUS_VALIDATING = "validating"
PLAN_STATUS_READY = "ready"
PLAN_STATUS_DEGRADED = "degraded"
PLAN_STATUS_NEEDS_USER_DECISION = "needs_user_decision"

# 版本产生原因
CHANGE_INITIAL = "initial"        # 全新首版
CHANGE_BRIEF = "brief_changed"    # 需求变化触发的重规划首版
CHANGE_REPAIR = "repair"          # 校验失败后的修复重排


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

    # ============================================================
    # 版本链与状态机（任务书 §4）
    # ============================================================
    # 与 brief_fingerprint 的分工：指纹是「变更检测的快速通道」（每轮比对，
    # 变了就 planning_reset）；版本号是「产物追溯的账本」——回答
    # 「这版行程基于哪个需求、哪份数据、从哪一版改来、为什么改」。
    plan_version: int = Field(default=1, ge=1, description="行程版本号；修复重排 +1")
    parent_plan_version: int | None = Field(
        default=None, description="修复自哪一版；重规划首版为 None（旧版已作废）")
    brief_version: int = Field(
        default=1, ge=1, description="生成本行程时的需求版本号快照")
    data_snapshot_version: str = Field(
        default="", description="候选池数据快照签名（poi_id+source 哈希）；空表示未记录")
    created_at: str = Field(
        default="", description="本版生成时刻（ISO 8601 UTC）；空表示未记录")
    change_reason: str = Field(
        default=CHANGE_INITIAL, description="本版产生原因：initial/repair/brief_changed")
    changed_fields: list[str] = Field(
        default_factory=list,
        description="相对上一版的变化字段（brief_changed=需求差异键；repair=修复动作摘要）")
    status: str = Field(
        default=PLAN_STATUS_VALIDATING,
        description="plan 状态机：validating → ready/degraded；failed 无产物不落此字段")

    def stamp_version(
        self,
        brief: TravelBrief,
        *,
        reason: str = CHANGE_INITIAL,
        parent: "Itinerary | None" = None,
        data_snapshot: str = "",
        changed_fields: list[str] | None = None,
    ) -> None:
        """在行程出生/重排时盖版本章（唯一写入点，避免各节点各写一套）。

        data_snapshot 传空表示沿用旧值（修复重排时候选池未变）。
        """
        # 延迟导入：顶层 import 会经 providers.travel.__init__ 拉起
        # tools.travel → cost.py → 本模块，形成循环初始化（实测 ImportError）
        from backend.providers.travel.facts import now_iso

        self.plan_version = (parent.plan_version + 1) if parent is not None else 1
        self.parent_plan_version = parent.plan_version if parent is not None else None
        self.brief_version = brief.version
        # 快照继承：显式传入优先；否则继承 parent（修复重排时候选池未变）；
        # 无 parent 时保持自身（新构造行程为空串，由调用方显式传入）
        self.data_snapshot_version = (
            data_snapshot
            or (parent.data_snapshot_version if parent is not None else "")
            or self.data_snapshot_version
        )
        self.created_at = now_iso()
        self.change_reason = reason
        self.changed_fields = list(changed_fields or [])
        self.status = PLAN_STATUS_VALIDATING

    def total_pois(self) -> int:
        return sum(len(d.visit_items()) for d in self.days)

    def all_pois(self) -> list[Poi]:
        return [i.poi for d in self.days for i in d.items if i.poi is not None]
