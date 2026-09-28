# -*- coding: utf-8 -*-
"""STOP B Step 1-5：Router 职责适配器契约测试。

这些测试只验证决策对象与旧实现的适配边界，不接入主图，也不执行 Tool、Skill
或 Workflow。外部分类/向量边界全部使用桩，避免测试依赖网络、模型或数据库。
"""
from __future__ import annotations

import ast

import pytest

from backend.orchestration.router.capability_router import CapabilityRouter
from backend.orchestration.router.domain_classifier import DomainPrediction
from backend.orchestration.router.domain_router import DomainRouter
from backend.orchestration.router.execution_mode import ExecutionModeResolver
from backend.orchestration.router.hierarchical import HierarchicalRouter
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
        assert all(set(candidate) == {"name", "score", "risk", "source"}
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
        return {
            "domain": "data",
            "capability": name,
            "candidates": candidates or ([] if name is None else [{"name": name}]),
            "confidence": 0.9 if name else 0.4,
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


def test_decisions_are_serializable():
    domain = _prediction("data")
    assert DomainRouter(_ClassifierStub(domain)).route("query", {})["domain"] == "data"
    decision = ExecutionModeDecision(
        mode="direct", target="sql.query", confidence=0.9, reasoning="test",
    )
    assert decision.to_dict()["target"] == "sql.query"


@pytest.mark.parametrize(
    "module_name",
    ["domain_router.py", "capability_router.py", "execution_mode.py"],
)
def test_adapters_do_not_import_execution_layers(module_name):
    path = __import__("pathlib").Path("orchestration/router", module_name)
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
