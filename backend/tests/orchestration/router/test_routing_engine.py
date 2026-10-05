"""RoutingEngine 契约测试。

这些测试只锁定路由编排边界：域判断、能力解析和执行方式决策必须由
唯一引擎串联，旧 Router 不得重新成为第二套生产决策入口。
"""
from __future__ import annotations

from backend.orchestration.router.engine import RoutingEngine
from backend.orchestration.router.types import (
    CapabilityScore,
    ExecutionMode,
    RouteDecision,
)
from backend.orchestration.router.models import ExecutionModeDecision


class _DomainRouter:
    def __init__(self, decision):
        self.decision = decision
        self.calls: list[tuple[str, dict]] = []

    def route(self, query, state=None, **_kwargs):
        self.calls.append((query, dict(state or {})))
        return self.decision


class _CapabilityRouter:
    def __init__(self, decision):
        self.decision = decision
        self.calls: list[tuple[str, str, dict]] = []

    def route(self, domain, query, context=None):
        self.calls.append((domain, query, dict(context or {})))
        return self.decision


class _ModeResolver:
    def __init__(self, decision):
        self.decision = decision
        self.calls: list[tuple[dict, dict, object]] = []

    def resolve(self, domain, capability, override=None):
        self.calls.append((domain, capability, override))
        return self.decision


class _RuleRouter:
    def __init__(self, decision=None):
        self.decision = decision

    def route(self, _query):
        return self.decision


class _IntentRouter:
    def __init__(self, decision):
        self.decision = decision
        self.calls: list[tuple[str, dict, object]] = []

    def classify(self, query, domain_decision, rule_decision=None, **_kwargs):
        self.calls.append((query, domain_decision, rule_decision))
        return self.decision


class _LLMRouter:
    def __init__(self, decision):
        self.decision = decision
        self.calls = 0

    def route(self, _query):
        self.calls += 1
        return self.decision


def _domain(domain="data", source="classifier"):
    return {
        "domain": domain,
        "subflow": None,
        "confidence": 0.91,
        "source": source,
        "reasoning": "test",
    }


def _capability():
    return {
        "domain": "data",
        "capability": "sql.query",
        "candidates": [{"name": "sql.query", "score": 0.91}],
        "confidence": 0.91,
        "source": "test",
        "reasoning": "test",
    }


def _direct_mode():
    return ExecutionModeDecision(
        mode="direct",
        target="sql.query",
        confidence=0.91,
        reasoning="test",
    )


def _intent(kind="task", source="test", intent="data"):
    return {
        "kind": kind,
        "intent": intent,
        "confidence": 0.91,
        "source": source,
        "reasoning": "test",
        "execution_hint": None,
        "candidate_names": [],
    }


def test_engine_composes_domain_capability_and_execution_decisions():
    domain_router = _DomainRouter(_domain())
    capability_router = _CapabilityRouter(_capability())
    mode_resolver = _ModeResolver(_direct_mode())
    engine = RoutingEngine(
        domain_router=domain_router,
        capability_router=capability_router,
        execution_resolver=mode_resolver,
        rule_router=_RuleRouter(),
    )

    decision = engine.route(
        "查库存",
        state={"routing_context": {"active_domain": "data"}},
    )

    assert decision.execution_mode is ExecutionMode.DIRECT
    assert decision.candidates[0].name == "sql.query"
    assert decision.routing_meta["domain"] == "data"
    assert domain_router.calls == [("查库存", {"routing_context": {"active_domain": "data"}})]
    assert capability_router.calls == [("data", "查库存", {"active_domain": "data"})]
    assert mode_resolver.calls[0][0] == _domain()


def test_engine_exposes_explicit_domain_intent_capability_policy_order():
    calls: list[str] = []

    class OrderedDomain(_DomainRouter):
        def route(self, query, state=None, **kwargs):
            calls.append("domain")
            return super().route(query, state, **kwargs)

    class OrderedIntent(_IntentRouter):
        def classify(self, query, domain_decision, rule_decision=None, **kwargs):
            calls.append("intent")
            return super().classify(query, domain_decision, rule_decision, **kwargs)

    class OrderedCapability(_CapabilityRouter):
        def route(self, domain, query, context=None):
            calls.append("capability")
            return super().route(domain, query, context)

    class OrderedPolicy(_ModeResolver):
        def resolve(self, domain, capability, override=None):
            calls.append("policy")
            return super().resolve(domain, capability, override)

    intent_router = OrderedIntent(_intent())
    engine = RoutingEngine(
        domain_router=OrderedDomain(_domain()),
        intent_router=intent_router,
        capability_router=OrderedCapability(_capability()),
        execution_resolver=OrderedPolicy(_direct_mode()),
        rule_router=_RuleRouter(),
        cache=_Cache(),
    )

    decision = engine.route("查库存")

    assert calls == ["domain", "intent", "capability", "policy"]
    assert decision.route_mode == "direct"
    assert decision.routing_meta["stage_order"] == [
        "entry_gate", "domain", "intent", "capability", "policy", "route_decision",
    ]
    assert decision.routing_meta["intent_kind"] == "task"
    assert intent_router.calls[0][1] == _domain()


def test_unknown_intent_becomes_clarify_without_capability_resolution():
    from backend.orchestration.router.execution_mode import ExecutionModeResolver

    capability = _CapabilityRouter(_capability())
    engine = RoutingEngine(
        domain_router=_DomainRouter(_domain("unknown", source="embedding")),
        intent_router=None,
        capability_router=capability,
        execution_resolver=ExecutionModeResolver(),
        rule_router=_RuleRouter(),
        cache=_Cache(),
    )

    decision = engine.route("帮我看看这个")

    assert capability.calls == []
    assert decision.execution_mode is ExecutionMode.PLAN
    assert decision.route_mode == "clarify"
    assert decision.routing_meta["need_clarification"] is True
    assert decision.routing_meta["intent_kind"] == "clarify"


def test_general_intent_maps_to_general_chat_route_mode():
    from backend.orchestration.router.execution_mode import ExecutionModeResolver

    capability = _CapabilityRouter(_capability())
    engine = RoutingEngine(
        domain_router=_DomainRouter(_domain("general")),
        capability_router=capability,
        execution_resolver=ExecutionModeResolver(),
        rule_router=_RuleRouter(),
        cache=_Cache(),
    )

    decision = engine.route("你好")

    assert capability.calls == []
    assert decision.execution_mode is ExecutionMode.PLAN
    assert decision.route_mode == "general_chat"
    assert decision.routing_meta["domain_action"] == "general_chat"


def test_engine_keeps_workflow_override_in_the_single_decision_path():
    workflow = RouteDecision(
        execution_mode=ExecutionMode.WORKFLOW,
        candidates=[CapabilityScore(name="daily_report", score=0.99)],
        confidence=0.99,
        reason="workflow signal",
        workflow_name="daily_report",
    )
    engine = RoutingEngine(
        domain_router=_DomainRouter(_domain()),
        capability_router=_CapabilityRouter(_capability()),
        execution_resolver=_ModeResolver(_direct_mode()),
        rule_router=_RuleRouter(workflow),
    )

    decision = engine.route("每天生成经营报表")

    assert decision.execution_mode is ExecutionMode.WORKFLOW
    assert decision.workflow_name == "daily_report"
    assert decision.routing_meta["decision_source"] == "rule_override"


def test_degraded_domain_uses_explicit_llm_fallback():
    fallback = RouteDecision(
        execution_mode=ExecutionMode.PLAN,
        candidates=[CapabilityScore(name="rag.search", score=0.3)],
        confidence=0.3,
        reason="fallback",
    )
    llm = _LLMRouter(fallback)
    engine = RoutingEngine(
        domain_router=_DomainRouter(_domain("unknown", source="degraded")),
        capability_router=_CapabilityRouter(_capability()),
        execution_resolver=_ModeResolver(_direct_mode()),
        rule_router=_RuleRouter(),
        llm_router=llm,
    )

    decision = engine.route("一个无法分类的问题")

    assert llm.calls == 1
    assert decision.reason == "fallback"
    assert decision.routing_meta["fallback_reason"] == "domain_classifier_degraded"
    assert decision.routing_meta["decision_source"] == "llm_fallback"


def test_compat_router_facade_delegates_to_engine(monkeypatch):
    import backend.orchestration.router.router as router_module

    expected = RouteDecision(
        execution_mode=ExecutionMode.DIRECT,
        candidates=[CapabilityScore(name="sql.query", score=0.9)],
        confidence=0.9,
        reason="engine",
    )

    class _Engine:
        def route(self, query, context=None):
            assert query == "查库存"
            assert context == {"tenant_id": "default"}
            return expected

    monkeypatch.setattr(router_module, "get_routing_engine", lambda: _Engine(), raising=False)
    facade = router_module.Router()

    assert facade.route("查库存", {"tenant_id": "default"}) is expected


def test_prefilter_miss_uses_engine_fallback_not_legacy_router(monkeypatch):
    import backend.orchestration.graph.routing.hierarchical as hierarchical_module
    import backend.orchestration.graph.travel_prefilter as travel_prefilter

    expected = RouteDecision(
        execution_mode=ExecutionMode.DIRECT,
        candidates=[CapabilityScore(name="travel.poi_search", score=0.9)],
        confidence=0.9,
        reason="engine fallback",
    )

    class _Engine:
        def route(self, query, state=None, **_kwargs):
            assert query == "查一下附近的景点"
            return expected

    monkeypatch.setattr(travel_prefilter, "try_travel_prefilter", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        hierarchical_module,
        "get_routing_engine",
        lambda: _Engine(),
        raising=False,
    )
    monkeypatch.setattr(
        hierarchical_module,
        "get_router",
        lambda: (_ for _ in ()).throw(AssertionError("不应调用旧 Router")),
        raising=False,
    )

    result = hierarchical_module._handle_hierarchical_meta(
        {
            "domain": "travel",
            "domain_confidence": 0.9,
            "domain_source": "classifier",
            "domain_action": "prefilter_travel",
            "candidate_tools": [],
        },
        {"question": "查一下附近的景点"},
        "查一下附近的景点",
        {},
    )

    assert result["route_mode"] == "direct"
    assert result["route_decision"]["reason"] == "engine fallback"


class _Cache:
    def __init__(self):
        self.values: dict[str, dict] = {}
        self.gets: list[str] = []
        self.sets: list[str] = []

    def get_json(self, key):
        self.gets.append(key)
        return self.values.get(key)

    def set_json(self, key, value, ttl=None):
        del ttl
        self.sets.append(key)
        self.values[key] = value


def test_engine_cache_ignores_dynamic_session_fields_but_keeps_tenant_scope():
    from backend.orchestration.router.engine import _route_cache_key

    first = {
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "department": "sales",
        "routing_context": {
            "active_domain": "travel",
            "last_action": "poi.search",
            "pending_question": "出发日期？",
        },
    }
    second = {
        **first,
        "routing_context": {
            "active_domain": "customer_service",
            "last_action": "order.query",
            "pending_question": "订单号？",
        },
    }

    assert _route_cache_key("  查库存  ", first) == _route_cache_key(
        "查库存", second,
    )
    assert _route_cache_key("查库存", first) != _route_cache_key(
        "查库存", {**first, "user_id": "user-b"},
    )
    assert _route_cache_key("查库存", first) != _route_cache_key(
        "查库存", {**first, "tenant_id": "tenant-b"},
    )


def test_engine_cache_hit_skips_all_route_providers():
    cache = _Cache()
    domain = _DomainRouter(_domain())
    capability = _CapabilityRouter(_capability())
    resolver = _ModeResolver(_direct_mode())
    engine = RoutingEngine(
        domain_router=domain,
        capability_router=capability,
        execution_resolver=resolver,
        rule_router=_RuleRouter(),
        cache=cache,
    )

    first = engine.route("查库存", {"tenant_id": "tenant-a", "user_id": "u1"})
    second = engine.route("查库存", {"tenant_id": "tenant-a", "user_id": "u1"})

    assert first.execution_mode == second.execution_mode == ExecutionMode.DIRECT
    assert len(domain.calls) == 1
    assert len(capability.calls) == 1
    assert len(resolver.calls) == 1
    assert len(cache.sets) == 1
    assert second.routing_meta["cache"] == "hit"


def test_engine_does_not_cache_infrastructure_fallback():
    cache = _Cache()
    fallback = RouteDecision(
        execution_mode=ExecutionMode.PLAN,
        candidates=[],
        confidence=0.0,
        reason="fallback",
    )
    engine = RoutingEngine(
        domain_router=_DomainRouter(_domain("unknown", source="degraded")),
        capability_router=_CapabilityRouter(_capability()),
        execution_resolver=_ModeResolver(_direct_mode()),
        rule_router=_RuleRouter(),
        llm_router=_LLMRouter(fallback),
        cache=cache,
    )

    engine.route("依赖故障", {"tenant_id": "tenant-a"})
    engine.route("依赖故障", {"tenant_id": "tenant-a"})

    assert cache.sets == []


def test_vector_failure_uses_explicit_llm_fallback(monkeypatch):
    from backend.orchestration.router.vector_router import VectorRouteError

    fallback = RouteDecision(
        execution_mode=ExecutionMode.PLAN,
        candidates=[],
        confidence=0.0,
        reason="llm fallback",
    )

    class BrokenCapability:
        def route(self, domain, query, context):
            raise VectorRouteError("vector_index_mismatch")

    llm = _LLMRouter(fallback)
    engine = RoutingEngine(
        domain_router=_DomainRouter(_domain()),
        capability_router=BrokenCapability(),
        execution_resolver=_ModeResolver(_direct_mode()),
        rule_router=_RuleRouter(),
        llm_router=llm,
        cache=_Cache(),
    )

    decision = engine.route("检索能力故障")

    assert llm.calls == 1
    assert decision.routing_meta["fallback_reason"] == "vector_index_mismatch"


def test_cache_key_changes_when_manifest_or_model_fingerprint_changes(monkeypatch):
    import backend.orchestration.router.engine as engine_module

    state = {"tenant_id": "tenant-a", "user_id": "u1"}
    monkeypatch.setattr(engine_module, "_manifest_fingerprint", lambda: "manifest-a")
    monkeypatch.setattr(engine_module, "_model_fingerprint", lambda: "model-a")
    first = engine_module._route_cache_key("查库存", state)
    monkeypatch.setattr(engine_module, "_manifest_fingerprint", lambda: "manifest-b")
    second = engine_module._route_cache_key("查库存", state)
    monkeypatch.setattr(engine_module, "_model_fingerprint", lambda: "model-b")
    third = engine_module._route_cache_key("查库存", state)

    assert first != second
    assert second != third
