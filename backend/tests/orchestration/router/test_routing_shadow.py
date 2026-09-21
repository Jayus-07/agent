# -*- coding: utf-8 -*-
"""test_routing_shadow.py — Router 架构开关与 shadow 双轨测试（2026-09-22）

覆盖：ROUTING_ARCHITECTURE=hierarchical 生效 / degraded 回退 legacy /
shadow 模式下 legacy 结果不变 + 对比记录 / tool_selector 对 hierarchical
fast_path 的直通（防重复 LLM 路由）。
"""
import pytest

import backend.orchestration.router.router as router_mod
from backend.orchestration.graph import tool_selector as ts
from backend.orchestration.router.types import (
    CapabilityScore,
    ExecutionMode,
    RouteDecision,
)


@pytest.fixture
def legacy_decision() -> RouteDecision:
    return RouteDecision(
        execution_mode=ExecutionMode.DIRECT,
        candidates=[CapabilityScore(name="rag.search", score=0.9)],
        confidence=0.9, reason="legacy",
    )


@pytest.fixture(autouse=True)
def _isolate_router_cache(monkeypatch):
    """路由缓存是 Redis 共享实例（跨测试/跨进程），单测内一律打桩隔离，
    防止用例间/与开发环境互相串决策。"""

    class _NoCache:
        def get_json(self, key):
            return None

        def set_json(self, key, value):
            return None

    monkeypatch.setattr(router_mod, "_router_cache", _NoCache())


class TestHierarchicalFlag:
    def test_hierarchical_branch_used(self, monkeypatch, legacy_decision):
        """flag=hierarchical → 调 HierarchicalRouter.route 并原样返回其决策。"""
        called = {}

        class StubHier:
            def route(self, query, context=None):
                called["query"] = query
                return RouteDecision(
                    execution_mode=ExecutionMode.DIRECT,
                    candidates=[CapabilityScore(name="sql.query", score=0.9)],
                    confidence=0.9,
                    routing_meta={"architecture": "hierarchical", "domain": "data"},
                )

        monkeypatch.setattr(
            "backend.orchestration.router.hierarchical.get_hierarchical_router",
            lambda: StubHier())
        monkeypatch.setattr(router_mod, "ROUTING_ARCHITECTURE", "hierarchical")

        r = router_mod.Router()
        d = r.route("查一下销售额")
        assert called["query"] == "查一下销售额"
        assert d.routing_meta["domain"] == "data"

    def test_degraded_falls_back_to_legacy(self, monkeypatch, legacy_decision):
        """粗分类 degraded（embedding 不可用）→ 抛错 → 回退 legacy 三层路由。"""

        class StubHier:
            def route(self, query, context=None):
                raise RuntimeError("coarse classifier degraded: EMBEDDING_UNAVAILABLE")

        monkeypatch.setattr(
            "backend.orchestration.router.hierarchical.get_hierarchical_router",
            lambda: StubHier())
        monkeypatch.setattr(router_mod, "ROUTING_ARCHITECTURE", "hierarchical")
        # legacy rule 层打桩：避免依赖关键词
        r = router_mod.Router()
        monkeypatch.setattr(r.rule, "route", lambda q: legacy_decision)

        d = r.route("查一下销售额")
        assert d.candidates[0].name == "rag.search"  # legacy 结果兜底


class TestShadowMode:
    def test_shadow_keeps_legacy_result_and_records(self, monkeypatch, legacy_decision):
        """shadow：legacy 结果原样返回；对比写入 trace.metadata（无 trace 时软跳过）。"""
        comparisons = []

        class StubHier:
            def shadow_compare(self, query, legacy):
                comparisons.append({
                    "legacy_tool": legacy.candidates[0].name,
                    "legacy_mode": legacy.execution_mode.value,
                    "hierarchical_domain": "knowledge",
                    "hierarchical_confidence": 0.9,
                    "hierarchical_tool": "rag.search",
                    "is_match": True,
                })
                return comparisons[-1]

        monkeypatch.setattr(
            "backend.orchestration.router.hierarchical.get_hierarchical_router",
            lambda: StubHier())
        monkeypatch.setattr(router_mod, "ROUTING_ARCHITECTURE", "legacy")
        monkeypatch.setattr(router_mod, "ROUTING_SHADOW_MODE", True)

        r = router_mod.Router()
        monkeypatch.setattr(r.rule, "route", lambda q: legacy_decision)
        # vector/llm 层不应被触达（rule 直接拍板）
        d = r.route("查一下销售额")
        assert d.candidates[0].name == "rag.search"
        assert len(comparisons) == 1
        assert comparisons[0]["is_match"] is True

    def test_shadow_off_no_comparison(self, monkeypatch, legacy_decision):
        monkeypatch.setattr(
            "backend.orchestration.router.hierarchical.get_hierarchical_router",
            lambda: pytest.fail("shadow 关闭时不应实例化 hierarchical"))
        monkeypatch.setattr(router_mod, "ROUTING_ARCHITECTURE", "legacy")
        monkeypatch.setattr(router_mod, "ROUTING_SHADOW_MODE", False)

        r = router_mod.Router()
        monkeypatch.setattr(r.rule, "route", lambda q: legacy_decision)
        d = r.route("查一下销售额")
        assert d.candidates[0].name == "rag.search"


class TestToolSelectorRespectsHierarchicalFastPath:
    def test_fast_path_marker_bypasses_fc(self, monkeypatch):
        """router 层已直选（tool_route_mode=fast_path）→ tool_selector 直通，
        绝不再进 FC LLM（防重复路由）。"""
        called = {"fc": False}
        monkeypatch.setattr(ts, "ENABLE_FC_TOOL_SELECTION", True)
        monkeypatch.setattr(
            ts, "_select_via_fc",
            lambda state, caps, t0: called.__setitem__("fc", True) or {})
        state = {
            "question": "生成本月经营报告",
            "session_id": "s1",
            "route_mode": "direct",
            "tool_route_mode": "fast_path",
            "route_decision": {"candidates": [{"name": "report.generate", "score": 0.9}]},
        }
        result = ts.tool_selector_node(state)
        assert called["fc"] is False
        assert result["_tool_selection"]["source"] == "passthrough"
        assert result["_tool_selection"]["reason"] == "hierarchical_fast_path"
