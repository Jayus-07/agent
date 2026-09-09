"""customer_service/router/types.py — 客服路由类型定义"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class CSDomain(str, Enum):
    """客服粗分类域（7 值）。"""
    KNOWLEDGE = "KNOWLEDGE"
    TRANSACTION = "TRANSACTION"
    AFTER_SALES = "AFTER_SALES"
    ACCOUNT = "ACCOUNT"
    COMPLAINT = "COMPLAINT"
    HUMAN = "HUMAN"
    UNKNOWN = "UNKNOWN"


class CSRoutePath(str, Enum):
    """客服路由路径（5 值）— 决定后续处理流程。"""
    KNOWLEDGE_QUERY = "knowledge_query"
    BUSINESS_QUERY = "business_query"
    BUSINESS_ACTION = "business_action"
    COMPLAINT_FLOW = "complaint_flow"
    HUMAN_HANDOFF = "human_handoff"


class CSDetection(BaseModel):
    """Domain Detector 输出 — 判定 query 是否属于客服域。"""
    is_cs: bool = False
    rule_hits: list[str] = Field(default_factory=list)
    rule_score: float = 0.0
    vector_score: float = 0.0
    reason: Optional[str] = None


class CSRouteResult(BaseModel):
    """CS Router 完整输出 — Domain Detector + Coarse + Fine 的综合结果。"""
    domain: CSDomain = CSDomain.UNKNOWN
    intent: str = "unknown"
    confidence: float = 0.0
    requires_auth: bool = False
    requires_action: bool = False
    risk_level: str = "low"
    route_path: CSRoutePath = CSRoutePath.KNOWLEDGE_QUERY
    kb_ids: list[str] = Field(default_factory=list)
    reason: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class IntentProfile(BaseModel):
    """单个意图的静态画像 — 描述该意图的执行需求。"""
    intent: str
    domain: CSDomain
    requires_auth: bool = False
    requires_action: bool = False
    risk_level: str = "low"
    route_path: CSRoutePath = CSRoutePath.KNOWLEDGE_QUERY
    kb_ids: list[str] = Field(default_factory=list)
