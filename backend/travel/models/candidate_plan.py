"""models/candidate_plan.py — 候选方案数据结构预留（任务书 §14，Phase 7）

**纯数据结构预留：P0 域图维持单方案产出，没有任何节点写入
``TravelGraphState.candidate_plans``；明确不做优化算法、不加 Planner Agent。**

预留动机：将来若要「出 A/B 两版行程让用户挑」，多方案比较需要一个
统一的对比形状——目标口径、成本、时长、违反情况、质量分。先定结构，
避免届时各处自造 dict 口径。字段语义：

- ``objective``：该方案优化的目标口径（如 "budget"（省钱）/ "pace"
  （宽松）/ "coverage"（覆盖必去最多）），自由字符串，比较层只展示不解释；
- ``violations``：该方案校验报告的违反码快照（含层级前缀，如
  "error:BUDGET_OVER"），供跨方案对比硬约束满足度；
- ``quality``：0-1 质量分（对齐 Itinerary.confidence 的计算口径），
  仅当方案经过 validator 时才非空。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from backend.providers.travel.facts import now_iso


class CandidatePlan(BaseModel):
    """候选方案（与 Itinerary 同级的多方案容器条目，Phase 7 预留）。"""

    candidate_id: str = Field(..., description="方案标识，如 cand_a / cand_b")
    objective: str = Field(..., description="该方案优化的目标口径")
    itinerary: dict = Field(..., description="完整行程快照（save_itinerary 形态）")
    cost_cny: float = Field(default=0.0, ge=0, description="预估总花费")
    total_minutes: int = Field(default=0, ge=0, description="全部活动+在途分钟数")
    violations: list[str] = Field(
        default_factory=list,
        description="校验违反码快照（level:CODE 形态），供跨方案对比")
    quality: float | None = Field(
        default=None, ge=0, le=1, description="0-1 质量分，未经 validator 时为空")
    created_at: str = Field(default_factory=now_iso)
