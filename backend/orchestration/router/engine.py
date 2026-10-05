"""RoutingEngine：主图唯一的业务路由编排入口。

Engine 只负责把域判断、能力解析和执行方式决策串成一条链路。
DomainRouter、CapabilityRouter 和 ExecutionModeResolver 各自只返回决策，
不执行 Tool、Skill 或 Workflow。
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any

from backend.infra.cache import get_cache
from backend.orchestration.router.capability_router import CapabilityRouter
from backend.orchestration.router.domain_router import DomainRouter
from backend.orchestration.router.execution_mode import ExecutionModeResolver
from backend.orchestration.router.llm_router import LLMRouter
from backend.orchestration.router.models import (
    CapabilityDecision,
    DomainDecision,
    ExecutionModeDecision,
)
from backend.orchestration.router.rule_router import RuleRouter
from backend.orchestration.router.vector_router import VectorRouteError
from backend.orchestration.router.types import (
    CapabilityScore,
    ExecutionMode,
    RouteDecision,
)


_ROUTE_ENGINE_CACHE_VERSION = "v2"


def _normalized_query(query: str) -> str:
    """归一化查询文本，避免空白差异造成无意义的缓存分叉。"""

    return " ".join(str(query or "").split()).casefold()


def _routing_scope(state: Mapping[str, Any] | None) -> dict[str, str]:
    """只提取会影响权限/个性化的稳定身份字段。

    ``routing_context`` 中的 active_domain、pending_question 等是会话状态，
    它们会持续变化，不得把一次会话状态污染到下一次路由缓存中。
    """

    state = state or {}
    context = state.get("routing_context")
    context = context if isinstance(context, Mapping) else {}
    result: dict[str, str] = {}
    for key in ("tenant_id", "user_id", "department"):
        value = state.get(key) or context.get(key) or ""
        result[key] = str(value)
    return result


def _manifest_fingerprint() -> str:
    """返回 capability/workflow 声明快照指纹。"""

    try:
        from backend.orchestration.router.manifest import load_manifest

        manifest = load_manifest()
        payload = {
            "capabilities": [
                {"name": item.name, "examples": list(item.examples)}
                for item in manifest.capabilities
            ],
            "workflows": [
                {"name": item.name, "examples": list(item.examples)}
                for item in manifest.workflows
            ],
            "domains": [
                {"name": item.name, "examples": list(item.examples)}
                for item in manifest.domains
            ],
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return "manifest-unknown"


def _model_fingerprint() -> str:
    """返回参与路由的模型角色版本，不暴露凭据。"""

    try:
        from backend.config.model_roles import resolve_effective

        values = {
            role: str(resolve_effective(role).get("value") or "")
            for role in ("main", "embedding")
        }
        raw = json.dumps(values, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return "model-unknown"


def _route_cache_key(
    query: str,
    state: Mapping[str, Any] | None = None,
) -> str:
    """构造企业级路由缓存键。

    键只包含版本、能力清单、模型版本、身份范围和查询摘要；不包含会话
    动态字段，也不把用户原文直接写进 Redis key。
    """

    normalized = _normalized_query(query)
    query_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    scope = _routing_scope(state)
    scope_text = json.dumps(scope, ensure_ascii=False, sort_keys=True)
    return "|".join(
        (
            "routing-engine",
            _ROUTE_ENGINE_CACHE_VERSION,
            f"manifest={_manifest_fingerprint()}",
            f"models={_model_fingerprint()}",
            f"scope={scope_text}",
            f"query={query_hash}",
        )
    )


class RoutingEngine:
    """把路由决策收口到一个明确的编排对象。"""

    def __init__(
        self,
        *,
        domain_router: DomainRouter | None = None,
        capability_router: CapabilityRouter | None = None,
        execution_resolver: ExecutionModeResolver | None = None,
        rule_router: RuleRouter | None = None,
        llm_router: LLMRouter | None = None,
        cache: Any | None = None,
    ) -> None:
        self.domain_router = domain_router or DomainRouter()
        self.capability_router = capability_router or CapabilityRouter()
        self.execution_resolver = execution_resolver or ExecutionModeResolver()
        self.rule_router = rule_router or RuleRouter()
        self.llm_router = llm_router or LLMRouter()
        self._cache = cache or get_cache("routing_engine", ttl=300)

    def route(
        self,
        query: str,
        state: Mapping[str, Any] | None = None,
        *,
        existing_override: Any | None = None,
    ) -> RouteDecision:
        """执行一条完整路由链并返回下游兼容的 ``RouteDecision``。"""

        state = state or {}
        cache_key = _route_cache_key(query, state)
        try:
            cached = self._cache.get_json(cache_key)
        except Exception:
            from backend.shared.logger import logger

            logger.debug("[RoutingEngine] 读取路由缓存失败，继续实时决策", exc_info=True)
            cached = None
        if cached is not None:
            self._record_cache("hit")
            decision = RouteDecision(**cached)
            meta = dict(decision.routing_meta or {})
            meta["cache"] = "hit"
            return decision.model_copy(update={"routing_meta": meta})

        self._record_cache("miss")
        decision = self._route_uncached(
            query,
            state,
            existing_override=existing_override,
        )
        if self._cacheable(decision):
            try:
                self._cache.set_json(cache_key, decision.model_dump())
            except Exception:
                from backend.shared.logger import logger

                logger.debug("[RoutingEngine] 写入路由缓存失败，不影响决策", exc_info=True)
        return decision

    def _route_uncached(
        self,
        query: str,
        state: Mapping[str, Any],
        *,
        existing_override: Any | None = None,
    ) -> RouteDecision:
        """执行不含缓存读写的单次决策。"""

        rule_override = self.rule_router.route(query)
        if self._is_execution_override(rule_override):
            return self._from_route_decision(
                rule_override,
                decision_source="rule_override",
            )

        try:
            domain_decision = self.domain_router.route(query, state)
        except Exception as exc:
            return self._llm_fallback(
                query,
                reason=f"domain_router_error:{type(exc).__name__}",
            )
        if domain_decision.get("source") in {"degraded", "fallback"}:
            return self._llm_fallback(
                query,
                reason="domain_classifier_degraded",
            )

        context = state.get("routing_context")
        try:
            capability_decision = self.capability_router.route(
                str(domain_decision.get("domain") or "unknown"),
                query,
                context if isinstance(context, Mapping) else {},
            )
        except VectorRouteError as exc:
            return self._llm_fallback(query, reason=exc.code)
        except Exception as exc:
            return self._llm_fallback(
                query,
                reason=f"capability_router_error:{type(exc).__name__}",
            )
        if capability_decision.get("fallback_reason"):
            return self._llm_fallback(
                query,
                reason=str(capability_decision["fallback_reason"]),
            )
        override = existing_override if existing_override is not None else rule_override
        execution_decision = self.execution_resolver.resolve(
            domain_decision,
            capability_decision,
            override,
        )
        return self._assemble(
            domain_decision,
            capability_decision,
            execution_decision,
            decision_source="domain_capability_policy",
        )

    @staticmethod
    def _cacheable(decision: RouteDecision) -> bool:
        """基础设施降级或澄清结果不缓存，避免故障被放大。"""

        meta = decision.routing_meta or {}
        if meta.get("fallback_reason"):
            return False
        return decision.execution_mode is not ExecutionMode.PLAN or bool(
            decision.candidates
        )

    @staticmethod
    def _is_execution_override(decision: RouteDecision | None) -> bool:
        if decision is None:
            return False
        if decision.execution_mode is ExecutionMode.WORKFLOW:
            return True
        return (
            decision.execution_mode is ExecutionMode.PLAN
            and decision.confidence >= 0.8
            and len(decision.candidates) >= 2
        )

    def _llm_fallback(self, query: str, *, reason: str) -> RouteDecision:
        self._record_fallback(reason)
        decision = self.llm_router.route(query)
        llm_meta = decision.routing_meta or {}
        llm_failed = llm_meta.get("decision_source") == "llm_failure"
        if llm_failed:
            self._record_fallback("llm_failure")
        return decision.model_copy(update={
            "routing_meta": {
                "architecture": "routing_engine",
                "decision_source": "llm_fallback",
                "fallback_reason": reason,
                "llm_failure": llm_failed,
                "domain": "",
                "domain_confidence": 0.0,
                "domain_margin": 0.0,
                "domain_source": "degraded",
                "domain_action": "fallback_llm",
                "candidate_tools": [candidate.name for candidate in decision.candidates],
                "candidate_tool_count": len(decision.candidates),
                "selected_tool": "",
                "need_clarification": False,
                "clarification_reason": "",
            },
        })

    @staticmethod
    def _record_cache(result: str) -> None:
        try:
            from backend.observability.metrics import record_router_cache

            record_router_cache(result)
        except Exception:
            from backend.shared.logger import logger

            logger.debug("[RoutingEngine] 路由缓存指标记录失败", exc_info=True)

    @staticmethod
    def _record_fallback(reason: str) -> None:
        try:
            from backend.observability.metrics import record_router_fallback

            record_router_fallback(reason)
        except Exception:
            from backend.shared.logger import logger

            logger.debug("[RoutingEngine] 路由降级指标记录失败", exc_info=True)

    @staticmethod
    def _from_route_decision(
        decision: RouteDecision,
        *,
        decision_source: str,
    ) -> RouteDecision:
        return decision.model_copy(update={
            "routing_meta": {
                "architecture": "routing_engine",
                "decision_source": decision_source,
                "fallback_reason": "",
                "domain": "",
                "domain_confidence": 0.0,
                "domain_margin": 0.0,
                "domain_source": "rule",
                "domain_action": decision.execution_mode.value,
                "candidate_tools": [candidate.name for candidate in decision.candidates],
                "candidate_tool_count": len(decision.candidates),
                "selected_tool": "",
                "need_clarification": False,
                "clarification_reason": "",
            },
        })

    @staticmethod
    def _assemble(
        domain: DomainDecision,
        capability: CapabilityDecision,
        execution: ExecutionModeDecision,
        *,
        decision_source: str,
    ) -> RouteDecision:
        mode = execution.mode
        if mode in {"general", "clarify", "domain_graph"}:
            route_mode = execution.target or mode
            output_mode = ExecutionMode.PLAN
        else:
            route_mode = mode
            output_mode = ExecutionMode(mode)

        rows = [
            CapabilityScore(
                name=str(row.get("name") or ""),
                score=float(row.get("score") or 0.0),
            )
            for row in capability.get("candidates") or []
            if row.get("name")
        ]
        selected = execution.target if mode == "direct" else ""
        meta = {
            "architecture": "routing_engine",
            "decision_source": decision_source,
            "fallback_reason": "",
            "domain": str(domain.get("domain") or "unknown"),
            "domain_confidence": float(domain.get("confidence") or 0.0),
            "domain_margin": float(domain.get("margin") or 0.0),
            "domain_second": str(domain.get("second_domain") or ""),
            "domain_source": str(domain.get("source") or ""),
            "domain_action": route_mode,
            "candidate_tools": [row.name for row in rows],
            "candidate_tool_count": len(rows),
            "fine_top1": capability.get("capability") or "",
            "fine_top1_score": float(capability.get("confidence") or 0.0),
            "fine_margin": 0.0,
            "tool_route_mode": "policy",
            "selected_tool": selected,
            "need_clarification": mode == "clarify",
            "clarification_reason": "" if mode != "clarify" else execution.reasoning,
            "routing_latency_ms": 0,
        }
        return RouteDecision(
            execution_mode=output_mode,
            candidates=rows,
            confidence=max(0.0, min(1.0, float(execution.confidence))),
            reason=execution.reasoning,
            workflow_name=execution.target if mode == "workflow" else None,
            routing_meta=meta,
        )


_engine: RoutingEngine | None = None


def get_routing_engine() -> RoutingEngine:
    """返回进程级路由引擎单例。"""

    global _engine
    if _engine is None:
        _engine = RoutingEngine()
    return _engine


__all__ = ["RoutingEngine", "get_routing_engine"]
