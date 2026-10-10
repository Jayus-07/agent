"""统一路由引擎的迁移门面与执行直通测试。"""

from backend.orchestration.graph import tool_selector as ts
import backend.orchestration.router.router as router_mod
from backend.orchestration.router.types import (
    CapabilityScore,
    ExecutionMode,
    RouteDecision,
)


def _decision() -> RouteDecision:
    return RouteDecision(
        execution_mode=ExecutionMode.DIRECT,
        candidates=[CapabilityScore(name="rag.search", score=0.9)],
        confidence=0.9,
        reason="统一引擎测试",
    )


def test_router_facade_only_delegates_to_routing_engine(monkeypatch):
    expected = _decision()
    calls: list[tuple[str, dict]] = []

    class StubEngine:
        def route(self, query, state):
            calls.append((query, state))
            return expected

    monkeypatch.setattr(router_mod, "get_routing_engine", lambda: StubEngine())
    facade = router_mod.Router()

    assert facade.route("查知识库", {"tenant_id": "default"}) is expected
    assert calls == [
        ("查知识库", {"tenant_id": "default"}),
    ]


def test_router_module_has_no_runtime_architecture_switches():
    source = open(router_mod.__file__, encoding="utf-8").read()
    assert "ROUTING_ARCHITECTURE" not in source
    assert "ROUTING_SHADOW_MODE" not in source
    assert "_route_legacy" not in source
    assert "_run_shadow" not in source


def test_tool_selector_respects_engine_fast_path(monkeypatch):
    """统一路由层已直选时，tool_selector 不得再次调用 FC。"""

    called = {"fc": False}
    monkeypatch.setattr(
        ts,
        "_select_via_fc",
        lambda state, caps, t0: called.__setitem__("fc", True) or {},
    )
    state = {
        "question": "查询库存",
        "domain": "data",
        "session_id": "s1",
        "route_mode": "direct",
        "tool_route_mode": "fast_path",
        "selected_tool": "sql.query",
        "route_decision": {
            "candidates": [
                {"name": "sql.query", "score": 0.9, "risk": "LOW",
                 "fast_path_enabled": True, "permission_ready": True},
                {"name": "data.collect", "score": 0.7, "risk": "HIGH",
                 "fast_path_enabled": False, "permission_ready": True},
            ],
            "routing_meta": {
                "domain": "data", "selection_mode": "fast_path",
                "selected_tool": "sql.query", "fine_top1_score": 0.9,
                "fine_top2": "data.collect", "fine_top2_score": 0.7,
                "fine_margin": 0.2, "risk_level": "LOW",
                "score_type": "vector_similarity_heuristic",
            },
        },
    }
    result = ts.tool_selector_node(state)
    assert called["fc"] is False
    assert result["_tool_selection"]["source"] == "passthrough"
    assert result["_tool_selection"]["reason"] == "validated_hierarchical_fast_path"
