"""企业级统一路由模块。

生产入口是 ``RoutingEngine``：域判断、域内能力候选和执行方式由同一条
决策链编排，规则/向量/LLM 只是可观测的证据提供者。``Router`` 仅是兼容
门面，不再承载第二套路由算法。
"""
from .types import (
    ExecutionMode,
    CapabilityScore,
    RouteDecision,
    ALL_CAPABILITIES,
    WORKFLOW_NAMES,
)
from .router import Router, get_router
from .engine import RoutingEngine, get_routing_engine
from .rule_router import RuleRouter
from .vector_router import VectorRouter, VectorRouteError
from .llm_router import LLMRouter
from .models import (
    CapabilityDecision,
    DomainDecision,
    ExecutionModeDecision,
)
from .domain_router import DomainRouter
from .capability_router import CapabilityRouter
from .execution_mode import ExecutionModeResolver

__all__ = [
    "ExecutionMode",
    "CapabilityScore",
    "RouteDecision",
    "ALL_CAPABILITIES",
    "WORKFLOW_NAMES",
    "Router",
    "get_router",
    "RoutingEngine",
    "get_routing_engine",
    "RuleRouter",
    "VectorRouter",
    "VectorRouteError",
    "LLMRouter",
    "DomainDecision",
    "CapabilityDecision",
    "ExecutionModeDecision",
    "DomainRouter",
    "CapabilityRouter",
    "ExecutionModeResolver",
]
