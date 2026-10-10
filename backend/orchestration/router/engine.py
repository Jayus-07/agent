"""RoutingEngine：主图唯一的业务路由编排入口。

Engine 固定编排：域判断 → 意图分类 → 候选能力解析 → 统一策略 →
RouteDecision。入口门禁由 GraphRunner 的 Input Guard 在进入本引擎前完成；
本引擎不复制安全门禁，也不执行 Tool、Skill 或 Workflow。
"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
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
from backend.observability.tracer import trace_collector


_ROUTE_ENGINE_CACHE_VERSION = "v4"
ROUTING_POLICY_VERSION = "routing-p0-1"
ROUTING_STAGE_ORDER = (
    "entry_gate",
    "domain",
    "intent",
    "capability",
    "policy",
    "route_decision",
)


@contextmanager
def _routing_stage(span_id: str, name: str, parent_id: str | None = None):
    """按真实执行边界记录路由阶段，异常只做 Trace 归因后继续上抛。

    parent_id 显式传入时不走 tracer 的"最近未关闭 span"推断——路由各阶段
    是 routing_engine 的子步骤，交给推断会平铺挂到外层 router 节点上，
    与 routing_engine 形成同级，层级丢失且耗时被重复聚合。
    """
    span = trace_collector.start_span(
        span_id, parent_id=parent_id, name=name, type="workflow", kind="router",
    )
    started = time.perf_counter()
    try:
        yield span
    except BaseException as exc:
        trace_collector.end_span(
            span,
            status="error",
            metrics={
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "error_type": type(exc).__name__,
            },
        )
        raise
    else:
        trace_collector.end_span(
            span,
            metrics={"elapsed_ms": round((time.perf_counter() - started) * 1000, 1)},
        )


def _ensure_routing_stages(cache_hit: bool, parent_id: str | None = None) -> None:
    """缓存短路时也显式标出未执行的阶段，Trace 仍保有完整阶段骨架。

    parent_id 与实时路径一致地指向 routing_engine，保证补齐的 skipped
    阶段和真实执行阶段在同一层级下，不会散落到外层 router 节点。
    """
    trace = trace_collector.current()
    if trace is None:
        return
    existing = {span.span_id for span in trace.spans}
    missing = {
        "entry_gate": "入口门禁",
        "domain": "领域判断",
        "intent": "意图分类",
        "capability": "能力解析",
        "policy": "执行策略",
        "route_decision": "路由决策",
    }
    for stage, label in missing.items():
        span_id = f"routing.{stage}"
        if span_id in existing:
            continue
        span = trace_collector.start_span(
            span_id, parent_id=parent_id, name=label, type="workflow", kind="router",
        )
        trace_collector.end_span(
            span,
            status="skipped",
            metrics={
                "reason": "routing_cache_hit" if cache_hit else "not_reached",
                "elapsed_ms": 0.0,
            },
        )


def _normalized_query(query: str) -> str:
    """归一化查询文本，避免空白差异造成无意义的缓存分叉。"""

    return " ".join(str(query or "").split()).casefold()


def _routing_scope(state: Mapping[str, Any] | None,
                   existing_override: Any | None = None) -> dict[str, Any]:
    """返回路由状态指纹输入，涵盖会话状态和服务端权限上下文。

    全部内容随后只以摘要进入缓存键，避免把查询上下文或身份明文写入 Redis key。
    """
    state = state or {}
    request_context = state.get("request_context")
    if isinstance(request_context, Mapping):
        auth = {
            key: request_context.get(key)
            for key in ("roles", "scopes", "permissions", "data_scope", "department")
        }
    else:
        auth = {
            key: getattr(request_context, key, None)
            for key in ("roles", "scopes", "permissions", "data_scope", "department")
        }
    guard = state.get("guard_result")
    if not isinstance(guard, Mapping):
        guard = {}
    return {
        "tenant_id": str(state.get("tenant_id") or ""),
        "user_id": str(state.get("user_id") or ""),
        "department": str(state.get("department") or auth.get("department") or ""),
        "session_id": str(state.get("session_id") or ""),
        "routing_context": {
            **(dict(state.get("routing_context")) if isinstance(state.get("routing_context"), Mapping) else {}),
            **{
                key: state.get(key)
                for key in (
                    "active_domain", "pending_question", "last_action",
                    "last_intent", "domain_hint", "clarification_request",
                )
                if state.get(key) is not None
            },
        },
        "authorization": auth,
        "state_authorization": {
            key: state.get(key)
            for key in ("roles", "scopes", "permissions", "data_scope")
            if state.get(key) is not None
        },
        "guard": {
            key: guard.get(key)
            for key in ("action", "risk_level", "needs_permission", "category")
        },
        "existing_override": existing_override,
    }


def _manifest_fingerprint() -> str:
    """返回 capability/workflow 声明快照指纹。"""

    try:
        from backend.orchestration.router.manifest import load_manifest

        manifest = load_manifest()
        payload = {
            "capabilities": [
                {
                    "name": item.name,
                    "examples": list(item.examples),
                    "routed": item.routed,
                    "domain": item.domain,
                    "risk_level": item.risk_level,
                    "fast_path_enabled": item.fast_path_enabled,
                }
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


def _prompt_fingerprint() -> str:
    """纳入当前路由 Prompt 版本，避免 Prompt 热更新后复用旧决策。"""
    try:
        from backend.prompts.service import prompt_service, template_hash

        with prompt_service._snapshot_lock:
            entry = prompt_service._snapshot.get("router.llm")
        payload = {
            "version": entry.version if entry else None,
            "template": template_hash(
                entry.template if entry else prompt_service._defaults.get("router.llm", "")
            ),
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return "prompt-unknown"


def _route_cache_key(
    query: str,
    state: Mapping[str, Any] | None = None,
    existing_override: Any | None = None,
) -> str:
    """构造企业级路由缓存键。

    会话状态、授权状态、Prompt/模型/能力注册及覆盖决策变化都会改变缓存键。
    """

    normalized = _normalized_query(query)
    query_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    scope = _routing_scope(state, existing_override)
    scope_text = json.dumps(scope, ensure_ascii=False, sort_keys=True, default=str)
    scope_hash = hashlib.sha256(scope_text.encode("utf-8")).hexdigest()[:24]
    return "|".join(
        (
            "routing-engine",
            _ROUTE_ENGINE_CACHE_VERSION,
            f"manifest={_manifest_fingerprint()}",
            f"models={_model_fingerprint()}",
            f"prompt={_prompt_fingerprint()}",
            f"policy={ROUTING_POLICY_VERSION}",
            f"scope={scope_hash}",
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
        with _routing_stage("routing_engine", "主路由引擎") as engine_span:
            _rid = engine_span.span_id
            # Input Guard 在 GraphRunner 中先于 RouterEngine 执行；该阶段记录
            # 路由入口已通过上游门禁，不复制或绕过门禁逻辑。
            with _routing_stage("routing.entry_gate", "入口门禁", _rid) as gate_span:
                guard = state.get("guard_result")
                gate_span.metrics["source"] = "graph_runner_input_guard"
                gate_span.metrics["action"] = (
                    guard.get("action", "") if isinstance(guard, Mapping) else "already_passed"
                )

            with _routing_stage("routing.cache_key", "路由缓存键计算", _rid):
                cache_key = _route_cache_key(query, state, existing_override)
            try:
                with _routing_stage("routing.cache_lookup", "路由缓存读取", _rid):
                    cached = self._cache.get_json(cache_key)
            except Exception:
                from backend.shared.logger import logger

                logger.debug("[RoutingEngine] 读取路由缓存失败，继续实时决策", exc_info=True)
                cached = None
                engine_span.metrics["cache_read_error"] = True

            if cached is not None:
                self._record_cache("hit")
                with _routing_stage("routing.route_decision", "路由决策", _rid) as decision_span:
                    decision = RouteDecision(**cached)
                    meta = dict(decision.routing_meta or {})
                    meta["cache"] = "hit"
                    out = decision.model_copy(update={
                        "route_mode": decision.route_mode
                        or meta.get("route_mode")
                        or decision.execution_mode.value,
                        "routing_meta": meta,
                    })
                    decision_span.metrics.update({
                        "cache": "hit",
                        "execution_mode": out.execution_mode.value,
                        "candidate_count": len(out.candidates),
                        "selection_mode": (out.routing_meta or {}).get("selection_mode", ""),
                        "score_type": (out.routing_meta or {}).get("score_type", ""),
                        "margin": (out.routing_meta or {}).get("fine_margin", 0.0),
                        "block_reason": (out.routing_meta or {}).get("block_reason", ""),
                        "final_destination": out.route_mode or out.execution_mode.value,
                        "route_policy_version": ROUTING_POLICY_VERSION,
                    })
                engine_span.metrics["cache"] = "hit"
                _ensure_routing_stages(cache_hit=True, parent_id=_rid)
                self._record_final_trace(out, cache="hit")
                self._record_decision_metrics(out, 0.0, verdict="cache_hit")
                return out

            self._record_cache("miss")
            _t0 = time.monotonic()
            decision = self._route_uncached(
                query,
                state,
                existing_override=existing_override,
                routing_parent=_rid,
            )
            with _routing_stage("routing.route_decision", "路由决策", _rid) as decision_span:
                decision_span.metrics.update({
                    "cache": "miss",
                    "execution_mode": decision.execution_mode.value,
                    "candidate_count": len(decision.candidates),
                    "selection_mode": (decision.routing_meta or {}).get("selection_mode", ""),
                    "score_type": (decision.routing_meta or {}).get("score_type", ""),
                    "margin": (decision.routing_meta or {}).get("fine_margin", 0.0),
                    "block_reason": (decision.routing_meta or {}).get("block_reason", ""),
                    "final_destination": decision.route_mode or decision.execution_mode.value,
                    "route_policy_version": ROUTING_POLICY_VERSION,
                })
            _ensure_routing_stages(cache_hit=False, parent_id=_rid)
            self._record_final_trace(decision, cache="miss")
            if self._cacheable(decision):
                try:
                    with _routing_stage("routing.cache_write", "路由缓存写入", _rid):
                        self._cache.set_json(cache_key, decision.model_dump())
                except Exception:
                    from backend.shared.logger import logger

                    logger.debug("[RoutingEngine] 写入路由缓存失败，不影响决策", exc_info=True)
                    engine_span.metrics["cache_write_error"] = True
            engine_span.metrics["cache"] = "miss"
            self._record_decision_metrics(decision, (time.monotonic() - _t0) * 1000)
            return decision

    def _route_uncached(
        self,
        query: str,
        state: Mapping[str, Any],
        *,
        existing_override: Any | None = None,
        routing_parent: str = "routing_engine",
    ) -> RouteDecision:
        """执行不含缓存读写的单次决策。"""
        _rid = routing_parent
        domain_error: Exception | None = None
        with _routing_stage("routing.domain", "领域判断", _rid) as domain_span:
            try:
                domain_decision = self.domain_router.route(query, state)
                domain_span.metrics.update({
                    "domain": str(domain_decision.get("domain") or "unknown"),
                    "source": str(domain_decision.get("source") or ""),
                })
            except Exception as exc:
                domain_error = exc
                domain_span.metrics["outcome"] = "degraded"
                domain_span.metrics["error_type"] = type(exc).__name__
                domain_decision = {
                    "domain": "unknown",
                    "subflow": None,
                    "confidence": 0.0,
                    "source": "degraded",
                    "reasoning": f"domain router error: {type(exc).__name__}",
                }

        with _routing_stage("routing.intent", "意图分类", _rid) as intent_span:
            try:
                with _routing_stage("routing.intent.rule_evidence", "规则意图证据", _rid) as rule_span:
                    rule_decision = self.rule_router.route(query)
                    rule_span.metrics["has_decision"] = rule_decision is not None
            except Exception as exc:
                # 规则是证据提供者，故障不能让整个请求崩溃；后续意图阶段
                # 仍可依据域判断继续工作。
                from backend.shared.logger import logger

                logger.warning("[RoutingEngine] 规则意图分类失败，忽略规则证据: %s", exc)
                intent_span.metrics["rule_evidence_error"] = type(exc).__name__
                rule_decision = None
            with _routing_stage("routing.intent.classifier", "结构化意图分类", _rid) as classifier_span:
                intent_decision = self.intent_router.classify(
                    query,
                    domain_decision,
                    rule_decision,
                    existing_override=existing_override,
                )
                classifier_span.metrics.update({
                    "kind": str(intent_decision.get("kind") or "unknown"),
                    "source": str(intent_decision.get("source") or ""),
                })
                intent_span.metrics["kind"] = str(intent_decision.get("kind") or "unknown")
        existing_decision = self._coerce_route_decision(existing_override)
        verified_context = self._capability_context(state)
        if self._is_execution_override(rule_decision, verified_context) or self._is_execution_override(
            existing_decision, verified_context,
        ):
            decision = (
                rule_decision
                if self._is_execution_override(rule_decision, verified_context)
                else existing_decision
            )
            with _routing_stage("routing.policy", "执行策略", _rid) as policy_span:
                policy_span.metrics["override"] = "rule" if self._is_execution_override(rule_decision, verified_context) else "existing"
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
            with _routing_stage("routing.policy", "执行策略", _rid) as policy_span:
                policy_span.metrics["fallback_reason"] = "domain_router_error"
                return self._llm_fallback(
                    query,
                    reason=f"domain_router_error:{type(domain_error).__name__}",
                    intent=intent_decision,
                )
        if domain_decision.get("source") in {"degraded", "fallback"}:
            with _routing_stage("routing.policy", "执行策略", _rid) as policy_span:
                policy_span.metrics["fallback_reason"] = "domain_classifier_degraded"
                return self._llm_fallback(
                    query,
                    reason="domain_classifier_degraded",
                    intent=intent_decision,
                )

        context = verified_context
        if intent_decision["kind"] in {"clarify", "general", "domain_graph"}:
            # 这些分支的归宿由域/意图本身确定，不再做一次无意义的能力
            # 向量解析；这样 clarify 不会因为未知域被误送 Planner。
            with _routing_stage("routing.capability", "能力解析", _rid) as capability_span:
                capability_span.metrics["skipped"] = True
                capability_span.metrics["reason"] = f"intent:{intent_decision['kind']}"
                capability_decision = self._empty_capability(domain_decision)
        else:
            try:
                with _routing_stage("routing.capability", "能力解析", _rid) as capability_span:
                    capability_decision = self.capability_router.route(
                        str(domain_decision.get("domain") or "unknown"),
                        query,
                        context,
                    )
                    capability_span.metrics.update({
                        "capability": str(capability_decision.get("capability") or ""),
                        "candidate_count": len(capability_decision.get("candidates") or []),
                        "fallback_reason": str(capability_decision.get("fallback_reason") or ""),
                    })
            except VectorRouteError as exc:
                with _routing_stage("routing.policy", "执行策略", _rid) as policy_span:
                    policy_span.metrics["fallback_reason"] = exc.code
                    return self._llm_fallback(
                        query, reason=exc.code, intent=intent_decision,
                        domain=domain_decision, context=context,
                    )
            except Exception as exc:
                with _routing_stage("routing.policy", "执行策略", _rid) as policy_span:
                    policy_span.metrics["fallback_reason"] = "capability_router_error"
                    return self._llm_fallback(
                        query,
                        reason=f"capability_router_error:{type(exc).__name__}",
                        intent=intent_decision,
                        domain=domain_decision,
                        context=context,
                    )

        with _routing_stage("routing.policy", "执行策略", _rid) as policy_span:
            override = existing_override
            if override is None and self._is_policy_override(rule_decision, verified_context):
                override = rule_decision
            if intent_decision["kind"] == "clarify" and override is None:
                override = {"route_mode": "clarify"}
            execution_decision = self.execution_resolver.resolve(
                domain_decision,
                capability_decision,
                override,
                verified_context=verified_context,
            )
            policy_span.metrics.update({
                "execution_mode": str(getattr(
                    execution_decision.mode, "value", execution_decision.mode,
                )),
                "target": execution_decision.target,
            })
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
    def _is_execution_override(
        decision: RouteDecision | Mapping[str, Any] | None,
        verified_context: Mapping[str, Any] | None = None,
    ) -> bool:
        if decision is None:
            return False
        mapping = decision if isinstance(decision, Mapping) else decision.model_dump()
        mode = mapping.get("execution_mode")
        mode = getattr(mode, "value", mode)
        confidence = float(mapping.get("confidence") or 0.0)
        candidates = mapping.get("candidates") or []
        try:
            from backend.orchestration.capability_registry import tool_registry
            from backend.orchestration.router.manifest import load_manifest

            manifest = load_manifest()
            if mode == ExecutionMode.WORKFLOW.value:
                workflow = str(mapping.get("workflow_name") or "")
                return workflow in {item.name for item in manifest.workflows}
            if mode != ExecutionMode.PLAN.value or confidence < 0.8 or len(candidates) < 2:
                # direct 旧 override 永远不是执行授权，必须重新经过 CapabilityRouter。
                return False
            allowed = set(manifest.planner_visible_capability_names)
            from backend.orchestration.router.capability_router import CapabilityRouter
            return all(
                (str(item.get("name") if isinstance(item, Mapping) else item) in allowed)
                and tool_registry.get_node(
                    str(item.get("name") if isinstance(item, Mapping) else item)
                ) is not None
                and CapabilityRouter._permission_ready(
                    str(item.get("name") if isinstance(item, Mapping) else item),
                    dict(verified_context or {}),
                )
                for item in candidates
            )
        except Exception:
            return False

    @classmethod
    def _is_policy_override(
        cls,
        decision: RouteDecision | None,
        verified_context: Mapping[str, Any] | None = None,
    ) -> bool:
        """强规则才可影响策略；弱规则只能作为候选提示。"""

        return cls._is_execution_override(decision, verified_context)

    @staticmethod
    def _empty_capability(domain: DomainDecision) -> CapabilityDecision:
        return {
            "domain": str(domain.get("domain") or "unknown"),
            "capability": None,
            "candidates": [],
            "confidence": 0.0,
            "selection_mode": "clarify",
            "top1": "",
            "top1_score": 0.0,
            "top2": "",
            "top2_score": 0.0,
            "margin": 0.0,
            "risk_level": "UNKNOWN",
            "score_type": "none",
            "fallback_reason": "",
            "block_reason": "policy_skip",
            "source": "policy_skip",
            "reasoning": "意图分支已确定，不进入候选能力解析",
        }

    def _llm_fallback(
        self,
        query: str,
        *,
        reason: str,
        intent: IntentDecision | None = None,
        domain: DomainDecision | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> RouteDecision:
        self._record_fallback(reason)
        domain_name = str((domain or {}).get("domain") or "unknown")
        allowed_names: list[str] = []
        if domain_name not in {"", "unknown", "general"}:
            try:
                from backend.orchestration.router.capability_router import (
                    _DOMAIN_ALIASES, CapabilityRouter,
                )
                from backend.orchestration.router.hierarchical import resolve_domain_tools

                canonical = _DOMAIN_ALIASES.get(domain_name, domain_name)
                allowed_names = [
                    c.name for c in CapabilityRouter._authorized_candidates(
                        resolve_domain_tools(canonical), dict(context or {}),
                    )
                ]
            except Exception:
                allowed_names = []
        try:
            decision = self.llm_router.route(
                query, domain=domain_name, allowed_candidates=allowed_names,
            )
        except Exception as exc:
            from backend.shared.logger import logger

            logger.warning("[RoutingEngine] 域内 LLM 分诊失败，安全澄清: %s", type(exc).__name__)
            decision = RouteDecision(
                execution_mode=ExecutionMode.PLAN,
                route_mode="clarify",
                candidates=[],
                confidence=0.0,
                reason="域内 LLM 分诊异常，安全澄清",
                routing_meta={
                    "decision_source": "llm_failure",
                    "selection_mode": "clarify",
                    "candidate_source": "none",
                    "candidate_tools": [],
                    "score_type": "none",
                    "confidence_used": False,
                    "fallback_reason": f"llm_exception:{type(exc).__name__}",
                    "block_reason": "llm_exception",
                },
            )
        llm_meta = decision.routing_meta or {}
        selection_mode = str(llm_meta.get("selection_mode") or "clarify")
        candidate_names = [item.name for item in decision.candidates]
        valid_llm_choice = (
            selection_mode == "llm_selection"
            and llm_meta.get("candidate_source") == "dynamic_registered_domain_candidates"
            and bool(allowed_names)
            and bool(candidate_names)
            and len(candidate_names) == len(set(candidate_names))
            and all(name in set(allowed_names) for name in candidate_names)
        )
        llm_failed = llm_meta.get("decision_source") == "llm_failure" or not valid_llm_choice
        if llm_meta.get("decision_source") == "llm_failure":
            self._record_fallback("llm_failure")
        if valid_llm_choice:
            # LLM 仅在合法候选内给 selector 提供排序提示；不使用其 score，
            # confidence 也保持 0。最终能力仍由 Tool Selector + 执行层复核。
            candidates = [
                CapabilityScore(name=item.name, score=0.0)
                for item in decision.candidates
            ]
            decision = decision.model_copy(update={
                "execution_mode": ExecutionMode.DIRECT,
                "route_mode": "direct",
                "candidates": candidates,
                "confidence": 0.0,
            })
        else:
            # 即使自定义 LLMRouter、旧 active Prompt 或测试替身返回了任意
            # RouteDecision，也必须在引擎边界再次拒绝未校验的能力名。
            selection_mode = "clarify"
            llm_failed = True
            decision = RouteDecision(
                execution_mode=ExecutionMode.PLAN,
                route_mode="clarify",
                candidates=[],
                confidence=0.0,
                reason=str(llm_meta.get("fallback_reason") or "llm_result_rejected"),
            )

        final_mode = decision.route_mode or decision.execution_mode.value
        out = decision.model_copy(update={
            "route_mode": final_mode,
            "routing_meta": {
                "architecture": "routing_engine",
                "stage_order": list(ROUTING_STAGE_ORDER),
                "decision_source": "llm_fallback",
                "fallback_reason": reason,
                "llm_failure": llm_failed,
                "route_policy_version": ROUTING_POLICY_VERSION,
                "domain": domain_name,
                "domain_confidence": float((domain or {}).get("confidence") or 0.0),
                "domain_margin": float((domain or {}).get("margin") or 0.0),
                "domain_source": str((domain or {}).get("source") or "degraded"),
                "domain_action": final_mode,
                "candidate_tools": [candidate.name for candidate in decision.candidates],
                "candidate_tool_count": len(decision.candidates),
                "selected_tool": "",
                "need_clarification": final_mode == "clarify",
                "clarification_reason": str(llm_meta.get("fallback_reason") or reason)
                    if final_mode == "clarify" else "",
                "selection_mode": selection_mode,
                "tool_route_mode": selection_mode,
                "candidate_source": "llm_router" if selection_mode == "llm_selection" else "none",
                "score_type": "llm_choice_untrusted" if selection_mode == "llm_selection" else "none",
                "fine_top1": decision.candidates[0].name if decision.candidates else "",
                "fine_top1_score": 0.0,
                "fine_top2": "",
                "fine_top2_score": 0.0,
                "fine_margin": 0.0,
                "risk_level": "UNKNOWN",
                "block_reason": str(llm_meta.get("block_reason") or (
                    "llm_result_rejected" if llm_failed and llm_meta.get("decision_source") != "llm_failure" else ""
                )),
                "final_destination": final_mode,
                "route_mode": final_mode,
                "intent": (intent or {}).get("intent", ""),
                "intent_kind": (intent or {}).get("kind", ""),
                "intent_confidence": float((intent or {}).get("confidence") or 0.0),
                "intent_source": (intent or {}).get("source", ""),
                "intent_reasoning": (intent or {}).get("reasoning", ""),
                "intent_execution_hint": (intent or {}).get("execution_hint"),
                "intent_candidates": list((intent or {}).get("candidate_names") or []),
            },
        })
        return out

    @staticmethod
    def _capability_context(state: Mapping[str, Any]) -> dict[str, Any]:
        context = state.get("routing_context")
        result = dict(context) if isinstance(context, Mapping) else {}
        request_context = state.get("request_context")
        if isinstance(request_context, Mapping):
            roles = request_context.get("roles") or ()
            scopes = request_context.get("scopes") or ()
        else:
            roles = getattr(request_context, "roles", ()) or ()
            scopes = getattr(request_context, "scopes", ()) or ()
        if request_context is None:
            try:
                from backend.core.request_context import get_tool_roles

                roles = get_tool_roles()
            except Exception:
                roles = ()
        result["_verified_roles"] = tuple(str(value) for value in roles)
        result["_verified_scopes"] = tuple(str(value) for value in scopes)
        return result

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
            "domain_score_type": str((domain or {}).get("score_type") or "unknown"),
            "domain_action": mode,
            "candidate_tools": [candidate.name for candidate in decision.candidates],
            "candidate_tool_count": len(decision.candidates),
            "selected_tool": "",
            "need_clarification": False,
            "clarification_reason": "",
            "route_mode": mode,
            "selection_mode": "rule_override",
            "tool_route_mode": "rule_override",
            "candidate_source": decision_source,
            "score_type": "rule_strength_heuristic",
            "fine_margin": 0.0,
            "risk_level": "UNKNOWN",
            "block_reason": "",
            "final_destination": mode,
            "route_policy_version": ROUTING_POLICY_VERSION,
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
        selection_mode = str(capability.get("selection_mode") or "clarify")
        fallback_reason = str(capability.get("fallback_reason") or "")
        block_reason = str(
            capability.get("block_reason")
            or capability.get("fast_path_block_reason")
            or (execution.reasoning if route_mode == "clarify" else "")
        )
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
            "domain_score_type": str(domain.get("score_type") or "unknown"),
            "domain_action": route_mode,
            "route_mode": route_mode,
            "route_policy_version": ROUTING_POLICY_VERSION,
            "intent": (intent or {}).get("intent", ""),
            "intent_kind": (intent or {}).get("kind", ""),
            "intent_confidence": float((intent or {}).get("confidence") or 0.0),
            "intent_source": (intent or {}).get("source", ""),
            "intent_reasoning": (intent or {}).get("reasoning", ""),
            "intent_execution_hint": (intent or {}).get("execution_hint"),
            "intent_candidates": list((intent or {}).get("candidate_names") or []),
            "candidate_tools": [row.name for row in rows],
            "candidate_tool_count": len(rows),
            "candidate_details": list(capability.get("candidates") or []),
            "candidate_source": str(capability.get("source") or ""),
            "fine_top1": capability.get("capability") or "",
            "fine_top1_score": float(capability.get("top1_score") or capability.get("confidence") or 0.0),
            "fine_top2": str(capability.get("top2") or ""),
            "fine_top2_score": float(capability.get("top2_score") or 0.0),
            "fine_margin": float(capability.get("margin") or 0.0),
            "tool_route_mode": selection_mode,
            "selection_mode": selection_mode,
            "score_type": str(capability.get("score_type") or "unknown"),
            "risk_level": str(capability.get("risk_level") or "UNKNOWN"),
            "fallback_reason": fallback_reason,
            "block_reason": block_reason,
            "selected_tool": selected,
            "need_clarification": route_mode == "clarify",
            "clarification_reason": "" if route_mode != "clarify" else execution.reasoning,
            "routing_latency_ms": 0,
            "final_destination": route_mode,
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

    @staticmethod
    def _record_final_trace(decision: RouteDecision, *, cache: str) -> None:
        """将统一门禁之后的最终路由快照写入 Trace metadata。"""
        try:
            trace = trace_collector.current()
            if trace is None:
                return
            meta = dict(decision.routing_meta or {})
            trace.metadata["routing_decision"] = {
                "selection_mode": meta.get("selection_mode", ""),
                "candidates": meta.get("candidate_details") or [
                    {"name": item.name, "score": item.score}
                    for item in decision.candidates
                ],
                "source": meta.get("candidate_source") or meta.get("decision_source", ""),
                "candidate_source": meta.get("candidate_source") or meta.get("decision_source", ""),
                "selection_source": "routing_engine",
                "score_type": meta.get("score_type", "unknown"),
                "margin": float(meta.get("fine_margin") or 0.0),
                "block_reason": meta.get("block_reason", ""),
                "selection_block_reason": "",
                "final_destination": meta.get("final_destination") or decision.route_mode,
                "route_policy_version": meta.get("route_policy_version") or ROUTING_POLICY_VERSION,
                "cache": cache,
            }
        except Exception:
            from backend.shared.logger import logger

            logger.debug("[RoutingEngine] 最终路由 Trace 写入失败", exc_info=True)


_engine: RoutingEngine | None = None


def get_routing_engine() -> RoutingEngine:
    """返回进程级路由引擎单例。"""

    global _engine
    if _engine is None:
        _engine = RoutingEngine()
    return _engine


__all__ = ["RoutingEngine", "get_routing_engine"]
