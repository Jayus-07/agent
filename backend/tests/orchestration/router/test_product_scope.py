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


def test_main_decision_keeps_classifier_evidence_for_trace():
    """2026-10-29 发货问排障：产品域分流保留粗分类证据。

    电商 FAQ 被误判客服域后收进 general 时，trace 里若没有原始判定域/
    次域/分差，「为什么直通 general_chat」无法解释。分流行为不变，
    只补证据链。
    """
    router = DomainRouter(classifier=_TravelClassifier())
    decision = router.route("旅行问题", {"product_id": "agent"})

    assert decision["domain"] == "general"
    assert decision["source"] == "product_scope"
    assert "pred=travel" in decision["reasoning"]
    assert decision["second_domain"] == "general"
    assert decision["margin"] == 0.5


class _UnknownButKnowledgeSecondClassifier:
    """复刻 2026-10-29 发货问线上形态：gate 拒判 unknown，次优域 knowledge。"""

    def classify(self, query: str, context=None):
        return SimpleNamespace(
            domain="unknown",
            confidence=0.717,
            source="gate",
            reason_code="LOW_CONFIDENCE",
            score_type="softmax_rank_score",
            margin=0.394,
            second_domain="knowledge",
            second_confidence=0.323,
        )


def test_agent_unknown_with_knowledge_second_falls_back_to_knowledge():
    """主助手低置信 + 次优域 knowledge → 转知识检索兜底，不落 clarify。

    电商 FAQ 口语问法在域心上无主时，原路径 unknown → clarify 让用户
    换说法；检索兜底由 rag Evidence Gate 把关（检索不到仍拒答）。
    """
    router = DomainRouter(classifier=_UnknownButKnowledgeSecondClassifier())

    agent_decision = router.route("客户下单后多久发货", {"product_id": "agent"})
    assert agent_decision["domain"] == "knowledge"
    assert agent_decision["source"] == "classifier"
    assert "知识检索兜底" in agent_decision["reasoning"]

    # 非主助手入口保持 unknown 原行为（ clarify 分派），不受兜底影响
    internal_decision = router.route("客户下单后多久发货", {"product_id": "internal"})
    assert internal_decision["domain"] == "unknown"


def test_agent_unknown_without_knowledge_second_keeps_unknown():
    """次优域不是 knowledge（如寒暄/技术闲聊）不触发兜底。"""

    class _UnknownGeneralSecondClassifier(_UnknownButKnowledgeSecondClassifier):
        def classify(self, query: str, context=None):
            ns = super().classify(query, context)
            ns.second_domain = "general"
            return ns

    router = DomainRouter(classifier=_UnknownGeneralSecondClassifier())
    decision = router.route("用一句话解释幂等", {"product_id": "agent"})
    assert decision["domain"] == "unknown"


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


def test_planner_visibility_filters_mcp_capability_by_tenant_grant():
    """PR #115 Codex P2：mcp.* 工具的可见性按 (product, capability, tenant)
    授权快照收窄——未授权租户的 Planner/Critique 渲染面不得出现该工具。"""
    from backend.core.product_context import filter_capabilities_for_state
    from backend.core.request_context import _current_tenant_id
    from backend.infra.mcp_registry import store as mcp_store
    from backend.infra.mcp_registry.store import McpRegistrySnapshot
    from backend.orchestration.router.manifest import CapabilityDecl

    decl = CapabilityDecl(
        name="mcp.echo", skill="mcp_dynamic", routed=True,
        description="回显查询（测试动态工具）",
        params_schema={"query": {"type": "string", "required": True}},
        planner_examples=({"query": "示例"},),
        examples=("回显示例一", "回显示例二"),
    )
    mcp_store.inject_snapshot_for_tests(McpRegistrySnapshot(
        loaded=True,
        capability_decls={"mcp.echo": decl},
        grants=frozenset({("agent", "mcp.echo", "tenant-a")}),
    ))
    try:
        state = {"product_id": "agent", "domain": "general"}

        tenant_token = _current_tenant_id.set("tenant-a")
        try:
            assert filter_capabilities_for_state(["mcp.echo"], state) == (
                "mcp.echo",)
        finally:
            _current_tenant_id.reset(tenant_token)

        tenant_token = _current_tenant_id.set("tenant-b")
        try:
            assert filter_capabilities_for_state(["mcp.echo"], state) == ()
        finally:
            _current_tenant_id.reset(tenant_token)
    finally:
        mcp_store.reset_for_tests()
