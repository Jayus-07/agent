"""router/types.py — 路由类型定义（2026-08-11）

核心原则（用户设计 review 后）：
  - execution_mode 只决定 HOW（执行方式），不决定 WHO（哪个 capability）
  - candidates 是带分数的能力候选，由 Planner 决定最终 DAG
  - Router 不承担业务判断（不做 mode + capability 绑定）
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class ExecutionMode(str, Enum):
    """执行方式（不绑定具体 capability）。

    - DIRECT: 单个 capability 直接执行（无需 Planner 生成 DAG）
    - PLAN: 复杂任务，Planner 用 candidates 生成最终 DAG
    - WORKFLOW: 已注册的工作流（daily_report / inventory_alert）
    """
    DIRECT = "direct"
    PLAN = "plan"
    WORKFLOW = "workflow"


class CapabilityScore(BaseModel):
    """单个 capability 的评分（Router 给的 hint，不是最终决定）。"""
    name: str = Field(..., description="capability 名，如 'sql.query'")
    score: float = Field(..., ge=0.0, le=1.0, description="置信度 0-1")


class RouteDecision(BaseModel):
    """路由决策（Router 输出，Planner 消费）。

    设计原则：
    - candidates 列表提供 hints，**不** 强制选
    - execution_mode 决定执行方式
    - Planner 决定最终 DAG（基于 candidates + query）
    """
    execution_mode: ExecutionMode
    candidates: List[CapabilityScore] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0, description="整体路由置信度")
    reason: Optional[str] = Field(None, description="路由判断依据")
    workflow_name: Optional[str] = Field(None, description="WORKFLOW 模式时指定 workflow 名")


# ── 已知 Capability 列表（Router 候选）──
ALL_CAPABILITIES = [
    "sql.query",
    "rag.search",
    "business.analyze",
    "report.generate",
    "email.send",
    "data.export",
    "web.search",
    "web.crawl",
    "data.collect",
]

# 已注册 Workflow 名（不在 ALL_CAPABILITIES 里）
WORKFLOW_NAMES = ["daily_report", "inventory_alert"]
