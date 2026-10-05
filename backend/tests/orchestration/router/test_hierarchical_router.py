# -*- coding: utf-8 -*-
"""test_hierarchical_router.py — 分层路由主流程单测（2026-09-22）

覆盖：Domain Tool Registry 解析 / Fast Path 三条件（top1/margin/risk+白名单）/
域图类域 prefilter 分派 / unknown 澄清 / rule override（workflow、复合意图）/
routing_meta 序列化契约 / tool_selector 对 hierarchical fast_path 的直通。
全部离线（粗分类与域内分数均为桩，不碰 pgvector/LLM）。
"""
import pytest

import backend.orchestration.router.hierarchical as hier
from backend.orchestration.router.domain_classifier import DomainPrediction
from backend.orchestration.router.hierarchical import (
    HierarchicalRouter,
    ToolCandidate,
    resolve_domain_tools,
)
from backend.orchestration.router.types import (
    CapabilityScore,
    ExecutionMode,
    RouteDecision,
)


def _prediction(domain: str, confidence: float = 0.9, margin: float = 0.5,
                source: str = "classifier") -> DomainPrediction:
    second = "general" if domain != "general" else "data"
    return DomainPrediction(
        domain=domain, confidence=confidence,
        second_domain=second,
        second_confidence=max(0.0, confidence - margin), margin=margin,
        source=source, reason_code="DOMAIN_CONFIDENT",
    )


@pytest.fixture
def router(monkeypatch):
    """粗分类打桩的 HierarchicalRouter（其余逻辑真实执行）。"""
    r = HierarchicalRouter()
    r.classifier = type("Stub", (), {"classify": staticmethod(lambda q, c=None: _prediction("data"))})()
    return r


class TestDomainToolRegistry:
    def test_knowledge_domain_tools(self):
        names = [c.name for c in resolve_domain_tools("knowledge")]
        assert names == ["rag.search", "web.search"]

    def test_data_domain_contains_sql(self):
        names = [c.name for c in resolve_domain_tools("data")]
        assert "sql.query" in names

    def test_risk_metadata_from_manifest(self):
        by_name = {c.name: c for c in resolve_domain_tools("communication")}
        assert by_name["email.send"].risk_level == "HIGH"
        assert by_name["email.send"].fast_path_enabled is False
        assert by_name["email.search"].risk_level == "LOW"

    def test_domain_graph_domains_have_no_main_capabilities(self):
        """CS/travel/selection 是域图：主图候选来自 travel 域 skill 例外。"""
        # customer_service 无主图 capability（CS 工具在域图内，不经主路由）
        assert resolve_domain_tools("customer_service") == []


class TestFineRouterFastPath:
    def test_fast_path_high_score_and_margin(self, router):
        router._fine_scores = lambda q, cands: {
            c.name: (0.90 if c.name == "sql.query" else 0.40) for c in cands}
        sel = router.select_tool("查一下销售额", "data", resolve_domain_tools("data"))
        assert sel.route_mode == "fast_path"
        assert sel.tool_name == "sql.query"
        assert sel.fine_top1_score >= 0.85
        assert sel.fine_margin >= 0.15

    def test_no_fast_path_on_low_margin(self, router):
        router._fine_scores = lambda q, cands: {
            c.name: (0.90 if c.name == "sql.query" else 0.80) for c in cands}
        sel = router.select_tool("查", "data", resolve_domain_tools("data"))
        assert sel.route_mode == "llm_selection"

    def test_high_risk_never_fast_path(self, router):
        """email.send 即使 0.99 分也禁止 Fast Path（§11）。"""
        cands = [ToolCandidate(name="email.send", risk_level="HIGH", fast_path_enabled=False)]
        router._fine_scores = lambda q, c: {"email.send": 0.99}
        sel = router.select_tool("发邮件", "communication", cands)
        assert sel.route_mode == "llm_selection"

    def test_disabled_fast_path_flag_blocks(self, router):
        """fast_path_enabled=false（如 data.export）即使高分也走灰区。"""
        cands = [ToolCandidate(name="data.export", risk_level="MEDIUM", fast_path_enabled=False)]
        router._fine_scores = lambda q, c: {"data.export": 0.95}
        sel = router.select_tool("导出数据", "data", cands)
        assert sel.route_mode == "llm_selection"

    def test_empty_candidates_need_clarification(self, router):
        sel = router.select_tool("q", "data", [])
        assert sel.need_clarification is True


class TestRouteDispatch:
    def test_tool_domain_direct_with_meta(self, router):
        d = router.route("查一下销售额")
        assert d.execution_mode == ExecutionMode.DIRECT
        meta = d.routing_meta
        assert meta["architecture"] == "hierarchical"
        assert meta["domain"] == "data"
        assert meta["domain_action"] == "tool_route"
        assert meta["tool_route_mode"] in ("fast_path", "llm_selection")
        assert meta["candidate_tools"], "候选列表非空"
        assert d.routing_meta["routing_latency_ms"] >= 0

    def test_cs_domain_delegates_to_prefilter(self, monkeypatch):
        r = HierarchicalRouter()
        r.rule = type("R", (), {"route": staticmethod(lambda q: None)})()
        r.classifier = type("Stub", (), {
            "classify": staticmethod(lambda q, c=None: _prediction("customer_service"))})()
        d = r.route("查一下我的订单物流")
        assert d.routing_meta["domain_action"] == "prefilter_cs"
        # 决策本体是 plan 占位（router_node 执行 prefilter）
        assert d.execution_mode == ExecutionMode.PLAN

    def test_unknown_clarify_action(self, monkeypatch):
        r = HierarchicalRouter()
        r.rule = type("R", (), {"route": staticmethod(lambda q: None)})()
        r.classifier = type("Stub", (), {
            "classify": staticmethod(lambda q, c=None: _prediction(
                "unknown", confidence=0.4, source="gate"))})()
        d = r.route("帮我看看这个")
        assert d.routing_meta["domain_action"] == "clarify"
        assert d.routing_meta["need_clarification"] is True

    def test_workflow_rule_override_wins(self):
        """「每天跑日报」workflow 强信号 → 既有 workflow 路径，不被粗分类改写。"""
        r = HierarchicalRouter()
        d = r.route("每天查询库存不足的商品并发送邮件提醒")
        assert d.execution_mode == ExecutionMode.WORKFLOW
        assert d.routing_meta["domain_action"] == "rule_workflow"

    def test_composite_intent_plan_override(self):
        """复合意图（连接词 + 双能力组）→ 既有 plan 编排，不拆单一工具。"""
        r = HierarchicalRouter()
        d = r.route("查询库存不足的商品，并说明员工报销制度")
        assert d.execution_mode == ExecutionMode.PLAN
        assert d.routing_meta["domain_action"] == "rule_composite"

    def test_routing_meta_serializable(self, router):
        """routing_meta 随 RouteDecision.model_dump 往返（路由缓存兼容）。"""
        d = router.route("查一下销售额")
        restored = RouteDecision(**d.model_dump())
        assert restored.routing_meta["domain"] == "data"
