from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.core.product_context import (
    AGENT_PRODUCT,
    CUSTOMER_SERVICE_PRODUCT,
    memory_user_id,
    namespaced_stream_id,
    namespace_session_id,
)
from backend.core.request_context import (
    _current_product_id,
    _current_product_source,
)
from backend.core.tool_governance.guard import GovernanceRuntime, ToolCallRequest
from backend.orchestration.router.capability_router import CapabilityRouter
from backend.orchestration.router.domain_router import (
    DomainRouter,
    _PREFILTER_DOMAIN_MAP,
)
from backend.orchestration.router.types import CapabilityScore


class _TravelClassifier:
    def classify(self, query: str, context=None):
        return SimpleNamespace(
            domain="travel",
            confidence=0.99,
            source="classifier",
            reason_code="travel_signal",
            score_type="embedding",
            margin=0.5,
            second_domain="general",
        )


def test_product_routes_preserve_history_keys_and_scope_stream_keys():
    # 旧 Agent Memory 归 Agent 产品所有，保持旧 user/session key 以续读存量数据。
    # 客服会话存于独立 customer_service 表，也保持原 conversation ID。
    assert namespace_session_id(AGENT_PRODUCT, "same") == "same"
    assert namespace_session_id(CUSTOMER_SERVICE_PRODUCT, "same") == "same"
    assert memory_user_id(AGENT_PRODUCT, "u1") == "u1"
    assert memory_user_id(CUSTOMER_SERVICE_PRODUCT, "u1") == "customer_service::u1"

    agent_stream = namespaced_stream_id(
        AGENT_PRODUCT, "request-1", user_id="u1", tenant_id="t1",
    )
    assert agent_stream != namespaced_stream_id(
        AGENT_PRODUCT, "request-1", user_id="u2", tenant_id="t1",
    )
    assert agent_stream != namespaced_stream_id(
        CUSTOMER_SERVICE_PRODUCT, "request-1", user_id="u1", tenant_id="t1",
    )


def test_namespaced_session_id_enforces_storage_limit():
    with pytest.raises(ValueError, match="会话标识过长"):
        namespace_session_id(AGENT_PRODUCT, "x" * 129)


def test_main_product_ignores_domain_hint_and_prefilter_exclusive_domain():
    router = DomainRouter(classifier=_TravelClassifier())
    state = {"product_id": "agent", "domain_hint": "customer_service"}

    classified = router.route("旅行问题", state)
    travel_route_mode = next(
        mode for mode, (domain, _) in _PREFILTER_DOMAIN_MAP.items()
        if domain == "travel"
    )
    prefiltered = router.route(
        "旅行问题", state,
        prefilter_update={"route_mode": travel_route_mode},
    )

    assert classified["domain"] == "general"
    assert prefiltered["domain"] == "general"
    assert classified["source"] == "product_scope"
    assert prefiltered["source"] == "product_scope"


def test_capability_candidates_are_limited_by_product_scope():
    candidates = [
        CapabilityScore(name="rag.search", score=0.9),
        CapabilityScore(name="travel.poi_search", score=0.8),
    ]

    allowed = CapabilityRouter._authorized_candidates(
        candidates,
        {"product_id": "agent"},
        effective_domain="knowledge",
    )

    assert [candidate.name for candidate in allowed] == ["rag.search"]


def test_tool_governance_rechecks_product_scope_at_execution():
    product_token = _current_product_id.set("agent")
    source_token = _current_product_source.set("registered_route")
    try:
        decision = GovernanceRuntime().evaluate(ToolCallRequest(
            capability="travel.poi_search",
            arguments={"city": "杭州"},
            candidate_capabilities=("travel.poi_search",),
            domain="travel",
            user_id="u1",
            tenant_id="t1",
        ))
    finally:
        _current_product_id.reset(product_token)
        _current_product_source.reset(source_token)

    assert decision.allowed is False
    assert decision.code == "product_capability_denied"


def test_tool_governance_rejects_shared_rag_in_customer_service_scope_for_agent():
    product_token = _current_product_id.set("agent")
    source_token = _current_product_source.set("registered_route")
    try:
        decision = GovernanceRuntime().evaluate(ToolCallRequest(
            capability="rag.search",
            arguments={"question": "订单退款状态"},
            candidate_capabilities=("rag.search",),
            domain="customer_service",
            user_id="u1",
            tenant_id="t1",
        ))
    finally:
        _current_product_id.reset(product_token)
        _current_product_source.reset(source_token)

    assert decision.allowed is False
    assert decision.code == "product_capability_denied"
