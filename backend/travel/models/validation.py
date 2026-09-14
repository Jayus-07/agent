"""travel/models/validation.py — 校验结果契约

validator 的纯输出：结构化 violations 列表。

为什么不做成「布尔 + 错误字符串」：
  - 域图需要按 code 决定修复动作（丢项 / 换序 / 标注），字符串无法稳定分派
  - 需要按 level 区分「必须修」与「提示用户」，否则修复器会把提示也当错误处理
  - 埋点按 code 聚合才能看出「哪条约束最常被违反」，字符串只能靠正则猜
"""
from __future__ import annotations

from pydantic import BaseModel, Field

LEVEL_ERROR = "error"
LEVEL_WARNING = "warning"

# ── 时间轴 ──
CODE_TIME_CLOSED = "TIME_CLOSED"               # 安排的到访时刻落在营业时段之外
CODE_TIME_CLOSED_WEEKDAY = "TIME_CLOSED_WEEKDAY"  # 安排到闭馆日
CODE_TIME_OVERLAP = "TIME_OVERLAP"             # 同一天两项时间重叠
CODE_TIME_DAY_OVERRUN = "TIME_DAY_OVERRUN"     # 超出一天的可用结束时刻
CODE_TIME_LONG_WAIT = "TIME_LONG_WAIT"         # 到达后需长时间等候开门（提示）

# ── 地理轴 ──
CODE_GEO_FAR_LEG = "GEO_FAR_LEG"               # 单段通勤过长
CODE_GEO_SCATTER = "GEO_SCATTER"               # 单日跨度过大（东西/南北反复横跳）
CODE_GEO_REVISIT = "GEO_REVISIT"               # 同一天重复到访同一 POI

# ── 体力轴 ──
CODE_PACE_TOO_MANY_POIS = "PACE_TOO_MANY_POIS"   # 单日 POI 数超出节奏档位
CODE_PACE_TOO_INTENSE = "PACE_TOO_INTENSE"       # 单日有效活动时长超出节奏档位

# ── 预算轴 ──
CODE_BUDGET_OVER = "BUDGET_OVER"               # 预算超支
CODE_BUDGET_TIGHT = "BUDGET_TIGHT"             # 逼近预算上限（预警，不算失败）

# ── 覆盖度 ──
CODE_MUST_GO_MISSING = "MUST_GO_MISSING"       # 用户点名必去的地点未落入行程（仅提示）


class Violation(BaseModel):
    """单条约束违反记录"""

    code: str = Field(..., description="约束码，见本模块 CODE_* 常量")
    level: str = Field(default=LEVEL_ERROR, description="error=必须修复 / warning=提示用户")
    message: str = Field(..., description="面向用户的中文说明")
    day_index: int = Field(default=0, description="所属天（1-based），0 表示全局约束")
    detail: dict = Field(default_factory=dict, description="结构化上下文，供修复器与埋点消费")


class ValidationReport(BaseModel):
    """一次完整校验的输出"""

    violations: list[Violation] = Field(default_factory=list)
    checked_days: int = Field(default=0, description="本次校验覆盖的天数")

    @property
    def errors(self) -> list[Violation]:
        return [v for v in self.violations if v.level == LEVEL_ERROR]

    @property
    def warnings(self) -> list[Violation]:
        return [v for v in self.violations if v.level == LEVEL_WARNING]

    @property
    def passed(self) -> bool:
        """无 error 级违反即通过 —— warning 不阻塞交付，但要写进行程单。"""
        return not self.errors

    def codes(self) -> list[str]:
        """违反的约束码（去重、保序），用于埋点聚合与测试断言。"""
        seen: list[str] = []
        for v in self.violations:
            if v.code not in seen:
                seen.append(v.code)
        return seen

    def summary(self) -> str:
        if not self.violations:
            return "全部硬约束校验通过"
        return (
            f"{len(self.errors)} 项需修复、{len(self.warnings)} 项提示："
            + "、".join(self.codes())
        )
