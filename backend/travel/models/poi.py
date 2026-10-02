"""travel/models/poi.py — POI 契约

一个 POI（Point of Interest）同时承载三件事，缺一不可：
  1. 排程所需的**时间信息**（营业时段 + 闭馆星期 + 建议停留）
  2. 排程所需的**地理信息**（经纬度，用于通勤折算与折返检测）
  3. 校验所需的**溯源信息**（source）—— 营业时间/票价是可证伪的事实，
     没有来源就必须标注，不允许以确定语气写进行程单。

source 为 "seed:local" 表示本地种子数据（P0 跑通链路用），
接入外部数据源（MCP）后应写为具体 provider 标识。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

# 闭馆星期的表示：0=周一 … 6=周日，与 datetime.date.weekday() 对齐
WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

CATEGORY_VISIT = "景点"
CATEGORY_MEAL = "美食"
CATEGORY_SHOPPING = "购物"
CATEGORY_NIGHT = "夜生活"
CATEGORY_PARK = "公园"

# ── 来源标识的解析口径（唯一一份）──────────────────────────────
# 约定：`provider[:variant]`，如 `seed:local`、`tencent:lbs`、`estimate:local`。
# 判定「这份数据是不是本地种子数据」**必须比提供方段**，不要用 str.startswith：
# 前者把 `seeded:cache` 这种将来新增的非种子来源正确排除，后者会把它误算成种子
# —— 后果是证据层标成 SEED、风险层误加「本地示例数据」免责声明、校验层误扣分。
# 结构病审查 P3-1：此前同一份判定被三处各写一遍（且都是 startswith），
# reporter 的 `describe_source` 用的又是按 `:` 切分，四份口径并不一致。
SOURCE_PROVIDER_SEED = "seed"


def source_provider(source: str | None) -> str:
    """来源标识的提供方段（``seed:local`` → ``seed``；无来源 → ``""``）。"""
    return (source or "").split(":")[0]


def is_seed_source(source: str | None) -> bool:
    """是否本地种子数据（按提供方段判定，理由见 SOURCE_PROVIDER_SEED 注释）。"""
    return source_provider(source) == SOURCE_PROVIDER_SEED


class Poi(BaseModel):
    """单个兴趣点的静态属性（不可变事实，不含排程结果）"""

    poi_id: str = Field(..., description="稳定标识，行程引用以此为准")
    name: str = Field(..., description="POI 名称")
    city: str = Field(..., description="所属城市键，需与 TravelBrief.destination 对齐")
    category: str = Field(default=CATEGORY_VISIT, description="类别，见 CATEGORY_* 常量")
    lat: float = Field(..., description="纬度")
    lng: float = Field(..., description="经度")
    open_time: str = Field(default="09:00", description="开放时刻 HH:MM")
    close_time: str = Field(default="17:00", description="闭馆时刻 HH:MM")
    closed_weekdays: list[int] = Field(
        default_factory=list, description="闭馆星期（0=周一），空表示全年开放"
    )
    suggested_minutes: int = Field(default=90, description="建议停留分钟数")
    ticket_cny: float = Field(default=0.0, description="门票单价（元），0 表示免费")
    tags: list[str] = Field(default_factory=list, description="偏好标签，与 PREFERENCE_KEYWORDS 的键对齐")
    rating: float = Field(default=0.0, description="热度/评分，用于候选排序")
    required: bool = Field(default=False, description="是否为用户点名必去（must_go 解析产物）")
    source: str = Field(default="seed:local", description="数据来源标识，用于行程单溯源")
    # —— 时效语义（Phase 1，任务书 §3）：事实必须自带「何时观测、是否核实」——
    # verification_status=unverified 时，营业时间/票价是占位值，reporter 必须明示，
    # 不允许以确定语气写进行程单。
    observed_at: str | None = Field(
        default=None,
        description="事实观测时间（ISO 8601 UTC）；None 表示未记录（种子数据静态事实）",
    )
    verification_status: str = Field(
        default="verified",
        description="verified=已核实（种子数据）；unverified=占位/未核实（外部解析补全）",
    )
    # —— 字段级状态（2026-10-02 拆分）：坐标与详情的核实语义分开 ——
    # live 检索（腾讯 LBS）坐标本身可信（实时检索 + observed_at），但票价/
    # 停留时长是占位 → verification_status=unverified 只描述详情；坐标可信
    # 用 location_status 独立表达，消费方（地图打点）按此判定，不再被
    # 详情占位连坐。空值=旧数据无此字段，回退按 source 前缀口径判定。
    location_status: str = Field(
        default="",
        description="坐标级核实状态：verified=坐标来自实时检索源；空=未单独标注（回退 source 口径）",
    )

    def is_open_on(self, weekday: int) -> bool:
        """给定星期是否开放（weekday: 0=周一）。"""
        return weekday not in self.closed_weekdays

    def closed_weekday_names(self) -> list[str]:
        """闭馆星期的中文名（用于 warnings 文案）。"""
        return [WEEKDAY_NAMES[d] for d in self.closed_weekdays if 0 <= d <= 6]
