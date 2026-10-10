# -*- coding: utf-8 -*-
"""STOP B Step 1-5：Router 职责适配器契约测试。

这些测试只验证决策对象与旧实现的适配边界，不接入主图，也不执行 Tool、Skill
或 Workflow。外部分类/向量边界全部使用桩，避免测试依赖网络、模型或数据库。
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

from backend.orchestration.router.capability_router import CapabilityRouter
from backend.orchestration.router.domain_classifier import DomainPrediction
from backend.orchestration.router.domain_router import DomainRouter
from backend.orchestration.router.execution_mode import ExecutionModeResolver
from backend.orchestration.router.hierarchical import HierarchicalRouter
from backend.orchestration.router.intent_router import IntentRouter
from backend.orchestration.router.models import (
    CapabilityDecision,
    DomainDecision,
    ExecutionModeDecision,
)


def _prediction(domain: str, source: str = "classifier") -> DomainPrediction:
    return DomainPrediction(
        domain=domain,
        confidence=0.91,
        second_domain="general",
        second_confidence=0.2,
        margin=0.71,
        source=source,
        reason_code="DOMAIN_CONFIDENT",
    )


class _ClassifierStub:
    def __init__(self, prediction: DomainPrediction):
        self.prediction = prediction

    def classify(self, query: str, context: dict | None = None):
        del query, context
        return self.prediction


class TestDomainRouter:
    @pytest.mark.parametrize(
        ("update", "domain", "subflow"),
        [
            ({"route_mode": "customer_service"}, "customer_service", None),
            ({"route_mode": "travel"}, "travel", "planning"),
            ({"route_mode": "selection_funnel"}, "selection_funnel", "funnel"),
            ({"route_mode": "travel_booking"}, "travel", "booking"),
            ({"route_mode": "travel_commerce"}, "travel", "commerce"),
        ],
    )
    def test_prefilter_update_normalizes_domain(self, update, domain, subflow):
        decision = DomainRouter(_ClassifierStub(_prediction("unknown"))).route(
            "query", {}, prefilter_update=update,
        )
        assert decision == {
            "domain": domain,
            "subflow": subflow,
            "confidence": 1.0,
            "score_type": "prefilter_signal",
            "source": "prefilter",
            "reasoning": f"prefilter 命中 route_mode={update['route_mode']}",
        }

    def test_cs_guard_clarify_keeps_customer_service_semantics(self):
        decision = DomainRouter(_ClassifierStub(_prediction("unknown"))).route(
            "不能处理", {},
            prefilter_update={"route_mode": "clarify", "final_answer": "请补充信息"},
        )
        assert decision["domain"] == "customer_service"
        assert decision["subflow"] == "clarify"

    def test_domain_hint_is_a_strong_domain_boundary(self):
        decision = DomainRouter(_ClassifierStub(_prediction("data"))).route(
            "下周去大阪怎么玩", {"domain_hint": "customer_service"},
        )
        assert decision["domain"] == "customer_service"
        assert decision["confidence"] == 1.0
        assert decision["source"] == "prefilter"

    @pytest.mark.parametrize(
        ("prediction_domain", "expected_domain", "expected_source"),
        [("data", "data", "embedding"), ("knowledge", "knowledge", "rule"),
         ("unknown", "unknown", "embedding")],
    )
    def test_classifier_result_is_only_normalized(self, prediction_domain,
                                                   expected_domain,
                                                   expected_source):
        source = "rule" if expected_source == "rule" else "classifier"
        decision = DomainRouter(_ClassifierStub(_prediction(
            prediction_domain, source=source))).route("query", {})
        assert decision["domain"] == expected_domain
        assert decision["source"] == expected_source
        assert decision["confidence"] == pytest.approx(0.91)


class TestCapabilityRouter:
    def test_returns_candidates_without_execution(self):
        hierarchical = HierarchicalRouter()
        hierarchical._fine_scores = lambda query, candidates: {
            candidate.name: (0.92 if candidate.name == "sql.query" else 0.3)
            for candidate in candidates
        }
        router = CapabilityRouter(hierarchical)
        decision = router.route("data", "查销售额")
        names = [candidate["name"] for candidate in decision["candidates"]]
        assert decision["domain"] == "data"
        assert decision["capability"] == "sql.query"
        assert "sql.query" in names
        assert all(set(candidate) == {
            "name", "score", "risk", "fast_path_enabled", "permission_ready", "source",
        }
                   for candidate in decision["candidates"])
        assert decision["source"] == "hierarchical"

    def test_empty_domain_has_no_candidate_and_does_not_call_execution(self):
        decision = CapabilityRouter().route("customer_service", "随便问问")
        assert decision["capability"] is None
        assert decision["candidates"] == []
        assert decision["source"] == "registry"

    def test_risk_metadata_is_preserved_for_email(self):
        hierarchical = HierarchicalRouter()
        hierarchical._fine_scores = lambda query, candidates: {
            candidate.name: 0.99 for candidate in candidates
        }
        decision = CapabilityRouter(hierarchical).route("communication", "发邮件")
        email = next(
            candidate for candidate in decision["candidates"]
            if candidate["name"] == "email.send"
        )
        assert email["risk"] == "HIGH"


class TestExecutionModeResolver:
    def setup_method(self):
        self.resolver = ExecutionModeResolver(
            workflow_names={"daily_report"},
            domain_graph_modes={
                "customer_service": "customer_service",
                "selection_funnel": "selection_funnel",
                "travel": "travel",
            },
        )

    @staticmethod
    def domain(domain: str, source: str = "embedding", subflow=None) -> DomainDecision:
        return {
            "domain": domain,
            "subflow": subflow,
            "confidence": 0.9,
            "source": source,
            "reasoning": "test",
        }

    @staticmethod
    def capability(name: str | None, candidates=None) -> CapabilityDecision:
        rows = candidates or ([] if name is None else [{
            "name": name, "score": 0.9, "risk": "LOW",
            "fast_path_enabled": True, "permission_ready": True,
        }])
        return {
            "domain": "data",
            "capability": name,
            "candidates": rows,
            "confidence": 0.9 if name else 0.4,
            "selection_mode": "fast_path" if name else "clarify",
            "top1": name or "",
            "top1_score": 0.9 if name else 0.0,
            "top2": "",
            "top2_score": 0.0,
            "margin": 0.9 if name else 0.0,
            "risk_level": "LOW" if name else "UNKNOWN",
            "score_type": "vector_similarity_heuristic" if name else "none",
            "source": "hierarchical",
            "reasoning": "test",
        }

    @pytest.mark.parametrize(
        ("domain", "capability", "expected"),
        [
            ("general", None, "general"),
            ("travel", None, "domain_graph"),
            ("data", "sql.query", "direct"),
            ("data", None, "plan"),
        ],
    )
    def test_public_modes(self, domain, capability, expected):
        source = "prefilter" if domain == "travel" else "embedding"
        decision = self.resolver.resolve(
            self.domain(domain, source=source), self.capability(capability),
        )
        assert decision.mode == expected

    def test_travel_subflow_maps_to_legacy_domain_graph_mode(self):
        decision = self.resolver.resolve(
            self.domain("travel", source="prefilter", subflow="booking"),
            self.capability(None),
        )
        assert decision.mode == "domain_graph"
        assert decision.target == "travel_booking"

    def test_workflow_override_wins_and_validates_registry(self):
        decision = self.resolver.resolve(
            self.domain("data"), self.capability("sql.query"),
            {
                "execution_mode": "workflow",
                "workflow_name": "daily_report",
                "confidence": 0.88,
            },
        )
        assert decision.mode == "workflow"
        assert decision.target == "daily_report"

    def test_clarify_remains_compat_route_mode(self):
        decision = self.resolver.resolve(
            self.domain("unknown"), self.capability(None),
            {"route_mode": "clarify"},
        )
        assert decision.mode == "plan"
        assert decision.target == "clarify"
        assert decision.compat_route_mode == "clarify"

    def test_capability_presence_without_selection_gate_clarifies(self):
        decision = self.resolver.resolve(
            self.domain("data"),
            {
                "domain": "data", "capability": "sql.query",
                "candidates": [{"name": "sql.query", "score": 0.99}],
                "confidence": 0.99, "source": "legacy", "reasoning": "untrusted",
            },
        )
        assert decision.mode == "plan"
        assert decision.target == "clarify"
        assert decision.compat_route_mode == "clarify"

    def test_gray_zone_has_no_direct_target(self):
        capability = self.capability("sql.query")
        capability["selection_mode"] = "llm_selection"
        decision = self.resolver.resolve(self.domain("data"), capability)
        assert decision.mode == "direct"
        assert decision.target is None

    def test_high_risk_fast_path_hint_is_demoted(self):
        decision = self.resolver.resolve(
            self.domain("communication"),
            {
                "domain": "communication", "capability": "email.send",
                "candidates": [{
                    "name": "email.send", "score": 0.99, "risk": "HIGH",
                    "fast_path_enabled": False, "permission_ready": True,
                }],
                "confidence": 0.99, "selection_mode": "fast_path",
                "top1": "email.send", "top1_score": 0.99, "top2": "",
                "top2_score": 0.0, "margin": 0.99,
                "risk_level": "HIGH", "score_type": "vector_similarity_heuristic",
                "source": "test", "reasoning": "test",
            },
        )
        assert decision.mode == "direct"
        assert decision.target is None


class TestIntentRouter:
    def test_unknown_domain_is_explicit_clarify(self):
        decision = IntentRouter().classify(
            "帮我看看这个",
            {
                "domain": "unknown",
                "subflow": None,
                "confidence": 0.2,
                "source": "embedding",
                "reasoning": "LOW_CONFIDENCE",
            },
        )
        assert decision["kind"] == "clarify"
        assert decision["execution_hint"] == "clarify"

    def test_prefilter_is_explicit_domain_graph_intent(self):
        decision = IntentRouter().classify(
            "规划大阪行程",
            {
                "domain": "travel",
                "subflow": "planning",
                "confidence": 1.0,
                "source": "prefilter",
                "reasoning": "prefilter",
            },
        )
        assert decision["kind"] == "domain_graph"
        assert decision["execution_hint"] == "domain_graph"


def test_decisions_are_serializable():
    domain = _prediction("data")
    assert DomainRouter(_ClassifierStub(domain)).route("query", {})["domain"] == "data"
    decision = ExecutionModeDecision(
        mode="direct", target="sql.query", confidence=0.9, reasoning="test",
    )
    assert decision.to_dict()["target"] == "sql.query"


def test_router_node_compat_update_adds_serializable_snapshots():
    from backend.orchestration.graph.router_node import _with_router_decisions

    result = _with_router_decisions(
        {"question": "帮我规划大阪3天行程"},
        {
            "route_decision": None,
            "route_mode": "travel",
        },
        "帮我规划大阪3天行程",
    )
    assert result["route_mode"] == "travel"
    assert result["domain_decision"]["domain"] == "travel"
    assert result["execution_decision"]["mode"] == "domain_graph"
    assert result["execution_decision"]["target"] == "travel"
    assert result["intent_decision"]["kind"] == "domain_graph"
    assert result["legacy_used"] is False


def test_router_node_engine_decision_becomes_capability_snapshot():
    from backend.orchestration.graph.router_node import _with_router_decisions
    from backend.orchestration.router.types import (
        CapabilityScore,
        ExecutionMode,
        RouteDecision,
    )

    decision = RouteDecision(
        execution_mode=ExecutionMode.DIRECT,
        candidates=[CapabilityScore(name="sql.query", score=0.9)],
        confidence=0.9,
        route_mode="direct",
        routing_meta={
            "domain": "data", "domain_confidence": 0.91,
            "domain_margin": 0.4, "domain_source": "classifier",
            "selection_mode": "fast_path", "tool_route_mode": "fast_path",
            "candidate_tools": ["sql.query"], "candidate_tool_count": 1,
            "candidate_details": [{
                "name": "sql.query", "score": 0.9, "risk": "LOW",
                "fast_path_enabled": True, "permission_ready": True,
            }],
            "fine_top1": "sql.query", "fine_top1_score": 0.9,
            "fine_top2": "", "fine_top2_score": 0.0, "fine_margin": 0.9,
            "score_type": "vector_similarity_heuristic", "risk_level": "LOW",
            "selected_tool": "sql.query", "route_policy_version": "routing-p0-1",
        },
    )
    result = _with_router_decisions(
        {"question": "查销售额"},
        {
            "route_decision": decision.model_dump(),
            "route_mode": "direct",
            "legacy_used": False,
        },
        "查销售额",
        existing_override=decision,
    )
    assert result["capability_decision"]["capability"] == "sql.query"
    assert result["execution_decision"]["mode"] == "direct"
    assert result["execution_decision"]["target"] == "sql.query"
    assert result["intent_decision"]["kind"] == "single"
    assert result["legacy_used"] is False


def test_router_node_records_sanitized_decision_in_current_trace_metadata():
    """缺少 trace metadata 注入时，此测试必须失败。"""
    from backend.observability.tracer import trace_collector
    from backend.orchestration.graph.router_node import _with_router_decisions
    from backend.orchestration.router.types import (
        CapabilityScore,
        ExecutionMode,
        RouteDecision,
    )

    trace_collector.clear_for_test()
    trace = trace_collector.start("测试问题", session_id="router-trace-test")
    decision = RouteDecision(
        execution_mode=ExecutionMode.DIRECT,
        candidates=[CapabilityScore(name="sql.query", score=0.93)],
        confidence=0.93,
        route_mode="direct",
        routing_meta={
            "domain": "data", "domain_confidence": 0.91,
            "domain_margin": 0.4, "domain_source": "classifier",
            "selection_mode": "fast_path", "tool_route_mode": "fast_path",
            "candidate_tools": ["sql.query"], "candidate_tool_count": 1,
            "candidate_details": [{
                "name": "sql.query", "score": 0.93, "risk": "LOW",
                "fast_path_enabled": True, "permission_ready": True,
            }],
            "fine_top1": "sql.query", "fine_top1_score": 0.93,
            "fine_top2": "", "fine_top2_score": 0.0, "fine_margin": 0.93,
            "score_type": "vector_similarity_heuristic", "risk_level": "LOW",
            "selected_tool": "sql.query", "route_policy_version": "routing-p0-1",
        },
    )
    try:
        _with_router_decisions(
            {"question": "查本月销售额"},
            {
                "route_decision": decision.model_dump(),
                "route_mode": "direct",
            },
            "查本月销售额",
            existing_override=decision,
        )

        assert trace.metadata["router"] == {
            "domain": "data",
            "subflow": None,
            "capability": "sql.query",
            "mode": "direct",
            "confidence": 0.93,
            "source": "route_engine",
            "intent": "sql.query",
            "intent_kind": "single",
            "intent_source": "route_engine",
            "intent_confidence": 0.93,
        }
    finally:
        trace_collector.clear_for_test()


@pytest.mark.parametrize(
    "module_name",
    ["domain_router.py", "intent_router.py", "capability_router.py", "execution_mode.py"],
)
def test_adapters_do_not_import_execution_layers(module_name):
    """适配器源码不得 import 执行层（tools/skills/planner/workflow/sql/rag）。

    路径从**模块自身**解析，不再用 cwd 相对路径——原实现
    ``Path("orchestration/router", module_name)`` 只在 cwd=backend/ 时成立，
    从仓库根运行时 3 例全部 FileNotFoundError，守卫在常规运行方式下静默失效。
    """
    module = importlib.import_module(
        f"backend.orchestration.router.{module_name[:-3]}"
    )
    path = Path(module.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    forbidden = ("tools", "skills", "planner", "workflow", "sql", "rag")
    assert not any(
        any(token in name.lower().split(".") for token in forbidden)
        for name in imported
    )
