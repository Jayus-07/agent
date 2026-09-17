"""selection_funnel/models/brief.py — 选品需求契约（漏斗域输入槽位）

Pydantic 纪律（踩过的坑）：字段名不得与字段类型同名。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class FunnelBrief(BaseModel):
    """漏斗域需求契约。

    category 是唯一必填槽位：没有类目，建池与类目阈值都无从谈起，
    槽位缺失时域图短路进追问（need_info），不猜测。
    """

    category: str = ""
    platform: str = ""
    # 价格带（元）；None = 不限
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    # 单件进货成本（元）；None = 用默认成本比估计
    max_unit_cost: Optional[float] = None
    # 目标贡献利润率（0-1）；None = 用配置默认（用户说"毛利率X%"映射到此）
    target_margin: Optional[float] = None
    # 推荐条数
    top_n: int = Field(default=5, ge=1, le=20)

    def missing_slots(self) -> list[str]:
        if self.category.strip():
            return []
        return ["category"]

    def is_runnable(self) -> bool:
        return not self.missing_slots()

    def validation_errors(self) -> list[str]:
        """入口硬校验：错误条件放行只会产出误导性空池，必须在入口拦下（need_info）。"""
        errors: list[str] = []
        if (self.price_min is not None and self.price_max is not None
                and self.price_min > self.price_max):
            errors.append(
                f"价格带 {self.price_min:g}-{self.price_max:g} 元下限大于上限")
        for name, value in (("价格带下限", self.price_min),
                            ("价格带上限", self.price_max),
                            ("进货成本", self.max_unit_cost)):
            if value is not None and value < 0:
                errors.append(f"{name} {value:g} 不能为负数")
        if (self.target_margin is not None
                and not 0 < self.target_margin < 1):
            errors.append(
                f"目标贡献利润率 {self.target_margin:g} 应在 0-100% 之间（如 30% 请写 0.3 或「毛利率30%」）")
        return errors

    def validation_warnings(self) -> list[str]:
        """入口软提示：不拦截运行，但要在报告/追问中如实披露。"""
        warnings: list[str] = []
        known = ("淘宝", "天猫", "拼多多", "京东", "抖音")
        if self.platform and self.platform not in known:
            warnings.append(
                f"平台「{self.platform}」不在常见清单（{'/'.join(known)}），"
                "仍按原样过滤；若无候选请核对平台名")
        return warnings
