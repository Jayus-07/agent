"""STOP E：State canonical source 与兼容投影测试。"""

from __future__ import annotations

import ast
from pathlib import Path

from backend.orchestration.state_projection import (
    canonical_route_decision,
    clarification_request,
    legacy_route_decision,
    resolved_params,
)


def _v2_decision() -> dict:
    return {
        "domain": {"name": "travel", "subflow": "planning"},
        "intent": {"name": "trip_plan"},
        "execution": {"mode": "workflow"},
        "interaction": {"mode": "execute"},
        "runtime": {
            "type": "workflow_runtime",
            "id": "travel",
            "subflow": "planning",
        },
        "capability": None,
        "confidence": 0.9,
    }


def test_canonical_decision_wins_over_legacy_snapshots():
    state = {
        "route_decision_v2": _v2_decision(),
        "route_decision": {"route_mode": "direct", "candidates": []},
        "domain_decision": {"domain": "general"},
    }

    assert canonical_route_decision(state)["runtime"]["id"] == "travel"


def test_legacy_projection_is_readable_when_only_v2_exists():
    projected = legacy_route_decision({"route_decision_v2": _v2_decision()})

    assert projected["route_mode"] == "travel"
    assert projected["execution_mode"] == "workflow"
    assert projected["routing_meta"]["domain"] == "travel"


def test_resolved_params_wins_and_legacy_tool_arguments_is_fallback():
    assert resolved_params({
        "resolved_params": {"report_type": "daily_sales"},
        "tool_arguments": {"report_type": "monthly_sales"},
    }) == {"report_type": "daily_sales"}
    assert resolved_params({
        "tool_arguments": {"report_type": "monthly_sales"},
    }) == {"report_type": "monthly_sales"}
    assert resolved_params({}) == {}


def test_clarification_request_is_canonical_with_legacy_fallback():
    marker = {"question": "需要目的地", "options": ["福州"]}
    assert clarification_request({
        "clarification_request": marker,
        "_clarify": {"question": "旧值"},
    }) == marker
    assert clarification_request({"_clarify": marker}) == marker
    assert clarification_request({
        "need_clarification": True,
        "clarification_reason": "LOW_CONFIDENCE",
    }) == {
        "question": "请补充一下您的需求：",
        "source": "LOW_CONFIDENCE",
    }
    assert clarification_request({}) is None


def test_tool_selector_no_longer_writes_legacy_tool_arguments():
    path = Path(__file__).resolve().parents[2] / "orchestration" / "graph" / "tool_selector.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        if any(
            isinstance(key, ast.Constant) and key.value == "tool_arguments"
            for key in node.keys
        ):
            hits.append(node.lineno)
    assert not hits, f"tool_selector 新增 legacy tool_arguments 写入: {hits}"
