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


# ── Capability / Workflow 名单：由 capabilities.yaml 派生（唯一事实源）──
# 手写清单已成历史：曾因 competitor.analyze 只在 rule_router 硬编码、
# ALL_CAPABILITIES 缺失，被 LLM Router 拒绝（见 manifest.py 模块注释）。
# 新增/修改 capability 一律改 capabilities.yaml，这里不许再手写。
from backend.orchestration.router.manifest import load_manifest

_manifest = load_manifest()
ALL_CAPABILITIES = [c.name for c in _manifest.routed_capabilities]
# 含 routed: false 的内部能力（workflow 内部消费，不对用户问题开放路由）
ALL_DECLARED_CAPABILITIES = [c.name for c in _manifest.capabilities]
WORKFLOW_NAMES = [w.name for w in _manifest.workflows]
