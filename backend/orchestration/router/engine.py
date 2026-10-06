"""RoutingEngine：主图唯一的业务路由编排入口。

Engine 固定编排：域判断 → 意图分类 → 候选能力解析 → 统一策略 →
RouteDecision。入口门禁由 GraphRunner 的 Input Guard 在进入本引擎前完成；
本引擎不复制安全门禁，也不执行 Tool、Skill 或 Workflow。
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import time
from typing import Any

from backend.infra.cache import get_cache
from backend.orchestration.router.capability_router import CapabilityRouter
from backend.orchestration.router.domain_router import DomainRouter
from backend.orchestration.router.execution_mode import ExecutionModeResolver
from backend.orchestration.router.intent_router import IntentRouter
from backend.orchestration.router.llm_router import LLMRouter
from backend.orchestration.router.models import (
    CapabilityDecision,
    DomainDecision,
    ExecutionModeDecision,
    IntentDecision,
)
from backend.orchestration.router.rule_router import RuleRouter
from backend.orchestration.router.vector_router import VectorRouteError
from backend.orchestration.router.types import (
    CapabilityScore,
    ExecutionMode,
    RouteDecision,
)


_ROUTE_ENGINE_CACHE_VERSION = "v3"
ROUTING_STAGE_ORDER = (
    "entry_gate",
    "domain",
    "intent",
    "capability",
    "policy",
    "route_decision",
)


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
        intent_router: IntentRouter | None = None,
        capability_router: CapabilityRouter | None = None,
        execution_resolver: ExecutionModeResolver | None = None,
        rule_router: RuleRouter | None = None,
        llm_router: LLMRouter | None = None,
        cache: Any | None = None,
    ) -> None:
        self.domain_router = domain_router or DomainRouter()
        self.intent_router = intent_router or IntentRouter()
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
            out = decision.model_copy(update={
                "route_mode": decision.route_mode
                or meta.get("route_mode")
                or decision.execution_mode.value,
                "routing_meta": meta,
            })
            self._record_decision_metrics(out, 0.0, verdict="cache_hit")
            return out

        self._record_cache("miss")
        _t0 = time.monotonic()
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
        self._record_decision_metrics(decision, (time.monotonic() - _t0) * 1000)
        return decision

    def _route_uncached(
        self,
        query: str,
        state: Mapping[str, Any],
        *,
        existing_override: Any | None = None,
    ) -> RouteDecision:
        """执行不含缓存读写的单次决策。"""
        domain_error: Exception | None = None
        try:
            domain_decision = self.domain_router.route(query, state)
        except Exception as exc:
            domain_error = exc
            domain_decision = {
                "domain": "unknown",
                "subflow": None,
                "confidence": 0.0,
                "source": "degraded",
                "reasoning": f"domain router error: {type(exc).__name__}",
            }

        try:
            rule_decision = self.rule_router.route(query)
        except Exception as exc:
            # 规则是证据提供者，故障不能让整个请求崩溃；后续意图阶段
            # 仍可依据域判断继续工作。
            from backend.shared.logger import logger

            logger.warning("[RoutingEngine] 规则意图分类失败，忽略规则证据: %s", exc)
            rule_decision = None

        intent_decision = self.intent_router.classify(
            query,
            domain_decision,
            rule_decision,
            existing_override=existing_override,
        )
        existing_decision = self._coerce_route_decision(existing_override)
        if self._is_execution_override(rule_decision) or self._is_execution_override(
            existing_decision
        ):
            decision = (
                rule_decision
                if self._is_execution_override(rule_decision)
                else existing_decision
            )
            return self._from_route_decision(
                decision,
                decision_source=(
                    "rule_override"
                    if self._is_execution_override(rule_decision)
                    else "existing_override"
                ),
                domain=domain_decision,
                intent=intent_decision,
            )

        if domain_error is not None:
            return self._llm_fallback(
                query,
                reason=f"domain_router_error:{type(domain_error).__name__}",
                intent=intent_decision,
            )
        if domain_decision.get("source") in {"degraded", "fallback"}:
            return self._llm_fallback(
                query,
                reason="domain_classifier_degraded",
                intent=intent_decision,
            )

        context = state.get("routing_context")
        if intent_decision["kind"] in {"clarify", "general", "domain_graph"}:
            # 这些分支的归宿由域/意图本身确定，不再做一次无意义的能力
            # 向量解析；这样 clarify 不会因为未知域被误送 Planner。
            capability_decision = self._empty_capability(domain_decision)
        else:
            try:
                capability_decision = self.capability_router.route(
                    str(domain_decision.get("domain") or "unknown"),
                    query,
                    context if isinstance(context, Mapping) else {},
                )
            except VectorRouteError as exc:
                return self._llm_fallback(query, reason=exc.code, intent=intent_decision)
            except Exception as exc:
                return self._llm_fallback(
                    query,
                    reason=f"capability_router_error:{type(exc).__name__}",
                    intent=intent_decision,
                )
            if capability_decision.get("fallback_reason"):
                return self._llm_fallback(
                    query,
                    reason=str(capability_decision["fallback_reason"]),
                    intent=intent_decision,
                )

        override = existing_override
        if override is None and self._is_policy_override(rule_decision):
            override = rule_decision
        if intent_decision["kind"] == "clarify" and override is None:
            override = {"route_mode": "clarify"}
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
            intent=intent_decision,
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
    def _coerce_route_decision(value: Any | None) -> RouteDecision | None:
        if isinstance(value, RouteDecision):
            return value
        if isinstance(value, Mapping):
            try:
                return RouteDecision(**dict(value))
            except Exception:
                return None
        return None

    @staticmethod
    def _is_execution_override(decision: RouteDecision | Mapping[str, Any] | None) -> bool:
        if decision is None:
            return False
        mapping = decision if isinstance(decision, Mapping) else decision.model_dump()
        mode = mapping.get("execution_mode")
        mode = getattr(mode, "value", mode)
        confidence = float(mapping.get("confidence") or 0.0)
        candidates = mapping.get("candidates") or []
        if mode == ExecutionMode.WORKFLOW.value:
            return True
        return (
            mode == ExecutionMode.PLAN.value
            and confidence >= 0.8
            and len(candidates) >= 2
        ) or (
            mode == ExecutionMode.DIRECT.value
            and confidence >= 0.8
            and bool(candidates)
        )

    @classmethod
    def _is_policy_override(cls, decision: RouteDecision | None) -> bool:
        """强规则才可影响策略；弱规则只能作为候选提示。"""

        return cls._is_execution_override(decision)

    @staticmethod
    def _empty_capability(domain: DomainDecision) -> CapabilityDecision:
        return {
            "domain": str(domain.get("domain") or "unknown"),
            "capability": None,
            "candidates": [],
            "confidence": 0.0,
            "source": "policy_skip",
            "reasoning": "意图分支已确定，不进入候选能力解析",
        }

    def _llm_fallback(
        self,
        query: str,
        *,
        reason: str,
        intent: IntentDecision | None = None,
    ) -> RouteDecision:
        self._record_fallback(reason)
        decision = self.llm_router.route(query)
        llm_meta = decision.routing_meta or {}
        llm_failed = llm_meta.get("decision_source") == "llm_failure"
        if llm_failed:
            self._record_fallback("llm_failure")
        return decision.model_copy(update={
            "route_mode": decision.route_mode or decision.execution_mode.value,
            "routing_meta": {
                "architecture": "routing_engine",
                "stage_order": list(ROUTING_STAGE_ORDER),
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
                "route_mode": decision.route_mode or decision.execution_mode.value,
                "intent": (intent or {}).get("intent", ""),
                "intent_kind": (intent or {}).get("kind", ""),
                "intent_confidence": float((intent or {}).get("confidence") or 0.0),
                "intent_source": (intent or {}).get("source", ""),
                "intent_reasoning": (intent or {}).get("reasoning", ""),
                "intent_execution_hint": (intent or {}).get("execution_hint"),
                "intent_candidates": list((intent or {}).get("candidate_names") or []),
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
    def _record_decision_metrics(decision: Any, latency_ms: float,
                                 verdict: str = "") -> None:
        """观测重构（2026-10-06）：每次决策统一写看板指标（软失败）。

        复活收口后无人写的 router_decision_total / router_layer_total /
        router_confidence / routing_domain_total / routing_hierarchy_verdict_total
        / routing_latency_seconds。verdict 缺省取 routing_meta.decision_source。
        """
        try:
            from backend.observability.metrics import (
                record_routing_engine_decision,
            )

            meta = decision.routing_meta or {}
            source = (meta.get("intent_source") or meta.get("domain_source")
                      or meta.get("decision_source") or "")
            v = verdict or str(meta.get("decision_source") or "routing_engine")
            confidence = (meta.get("intent_confidence")
                          if meta.get("intent_confidence") is not None
                          else meta.get("domain_confidence")) or 0.0
            record_routing_engine_decision(
                mode=getattr(decision, "route_mode", "") or meta.get("route_mode") or "",
                source=str(source),
                domain=str(meta.get("domain") or "unknown"),
                confidence=float(confidence),
                verdict=v,
                latency_ms=latency_ms,
            )
        except Exception:
            from backend.shared.logger import logger

            logger.debug("[RoutingEngine] 决策指标记录失败", exc_info=True)

    @staticmethod
    def _from_route_decision(
        decision: RouteDecision,
        *,
        decision_source: str,
        domain: DomainDecision | None = None,
        intent: IntentDecision | None = None,
    ) -> RouteDecision:
        mode = decision.execution_mode.value
        meta = {
            "architecture": "routing_engine",
            "stage_order": list(ROUTING_STAGE_ORDER),
            "decision_source": decision_source,
            "fallback_reason": "",
            "domain": str((domain or {}).get("domain") or ""),
            "domain_confidence": float((domain or {}).get("confidence") or 0.0),
            "domain_margin": float((domain or {}).get("margin") or 0.0),
            "domain_source": str((domain or {}).get("source") or ""),
            "domain_action": mode,
            "candidate_tools": [candidate.name for candidate in decision.candidates],
            "candidate_tool_count": len(decision.candidates),
            "selected_tool": "",
            "need_clarification": False,
            "clarification_reason": "",
            "route_mode": mode,
            "intent": (intent or {}).get("intent", ""),
            "intent_kind": (intent or {}).get("kind", ""),
            "intent_confidence": float((intent or {}).get("confidence") or 0.0),
            "intent_source": (intent or {}).get("source", ""),
            "intent_reasoning": (intent or {}).get("reasoning", ""),
            "intent_execution_hint": (intent or {}).get("execution_hint"),
            "intent_candidates": list((intent or {}).get("candidate_names") or []),
        }
        return decision.model_copy(update={
            "route_mode": mode,
            "routing_meta": meta,
        })

    @staticmethod
    def _assemble(
        domain: DomainDecision,
        capability: CapabilityDecision,
        execution: ExecutionModeDecision,
        *,
        decision_source: str,
        intent: IntentDecision | None = None,
    ) -> RouteDecision:
        mode = execution.mode
        compat_route_mode = getattr(execution, "compat_route_mode", None)
        if compat_route_mode:
            route_mode = compat_route_mode
            output_mode = ExecutionMode.PLAN
        elif mode in {"general", "clarify", "domain_graph"}:
            route_mode = execution.target or (
                "general_chat" if mode == "general" else mode
            )
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
            "stage_order": list(ROUTING_STAGE_ORDER),
            "decision_source": decision_source,
            "fallback_reason": "",
            "domain": str(domain.get("domain") or "unknown"),
            "domain_confidence": float(domain.get("confidence") or 0.0),
            "domain_margin": float(domain.get("margin") or 0.0),
            "domain_second": str(domain.get("second_domain") or ""),
            "domain_source": str(domain.get("source") or ""),
            "domain_action": route_mode,
            "route_mode": route_mode,
            "intent": (intent or {}).get("intent", ""),
            "intent_kind": (intent or {}).get("kind", ""),
            "intent_confidence": float((intent or {}).get("confidence") or 0.0),
            "intent_source": (intent or {}).get("source", ""),
            "intent_reasoning": (intent or {}).get("reasoning", ""),
            "intent_execution_hint": (intent or {}).get("execution_hint"),
            "intent_candidates": list((intent or {}).get("candidate_names") or []),
            "candidate_tools": [row.name for row in rows],
            "candidate_tool_count": len(rows),
            "fine_top1": capability.get("capability") or "",
            "fine_top1_score": float(capability.get("confidence") or 0.0),
            "fine_margin": 0.0,
            "tool_route_mode": "policy",
            "selected_tool": selected,
            "need_clarification": route_mode == "clarify",
            "clarification_reason": "" if route_mode != "clarify" else execution.reasoning,
            "routing_latency_ms": 0,
        }
        return RouteDecision(
            execution_mode=output_mode,
            route_mode=route_mode,
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
