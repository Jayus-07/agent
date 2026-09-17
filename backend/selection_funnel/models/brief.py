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
    # 目标毛利率（0-1）；None = 用配置默认
    target_margin: Optional[float] = None
    # 推荐条数
    top_n: int = Field(default=5, ge=1, le=20)

    def missing_slots(self) -> list[str]:
        if self.category.strip():
            return []
        return ["category"]

    def is_runnable(self) -> bool:
        return not self.missing_slots()
