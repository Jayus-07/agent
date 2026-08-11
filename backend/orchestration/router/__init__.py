"""router — 企业级 RAG/SQL/Workflow 混合路由（2026-08-11）

设计原则：
- Router 只给 hints（candidates + 分数），不决定最终 DAG
- Planner 用 hints 生成最终 DAG
- 3 层 fallback：Rule → Embedding → LLM

链路:
  用户问题
    ↓
  Rule Router（强信号，1ms）
    ↓ confidence < 0.8
  Embedding Router（语义匹配，复用 Chroma，~30ms）
    ↓ confidence < 0.85
  LLM Router（qwen 兜底，~3-5s）
    ↓
  RouteDecision
    ↓
  LangGraph conditional_edges
    ↓
  DIRECT / PLAN / WORKFLOW
"""
from .types import (
    ExecutionMode,
    CapabilityScore,
    RouteDecision,
    ALL_CAPABILITIES,
    WORKFLOW_NAMES,
)
from .router import Router, get_router
from .rule_router import RuleRouter
from .vector_router import VectorRouter
from .llm_router import LLMRouter

__all__ = [
    "ExecutionMode",
    "CapabilityScore",
    "RouteDecision",
    "ALL_CAPABILITIES",
    "WORKFLOW_NAMES",
    "Router",
    "get_router",
    "RuleRouter",
    "VectorRouter",
    "LLMRouter",
]
