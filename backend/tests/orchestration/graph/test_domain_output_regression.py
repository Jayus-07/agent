"""STOP D：域图既有输出字段与统一结果字段并存回归。"""

from __future__ import annotations

from backend.orchestration.graph.cs_graph_node import _build_main_state_update
from backend.orchestration.graph.selection_funnel_graph_node import (
    selection_funnel_graph_node,
)
from backend.orchestration.graph.travel_graph_node import _build_main_state_update as build_travel_update


def test_cs_adapter_keeps_existing_main_state_fields():
    update = _build_main_state_update(
        {},
        {"final_answer": "客服回答", "action_result": {"ok": True}},
    )
    assert update["final_answer"] == "客服回答"
    assert update["cs_action_result"] == {"ok": True}


def test_travel_adapter_keeps_existing_main_state_fields():
    update = build_travel_update(
        {
            "final_answer": "行程回答",
            "travel_context": {"itinerary": {"days": []}},
        }
    )
    assert update["final_answer"] == "行程回答"
    assert update["travel_context"] == {"itinerary": {"days": []}}


def test_selection_adapter_does_not_rewrite_domain_report(monkeypatch):
    # 只验证异常出口仍保持原有报告字段；不启动真实域图。
    monkeypatch.setattr(
        "backend.orchestration.graph.selection_funnel_graph_node.get_selection_funnel_graph",
        lambda: (_ for _ in ()).throw(RuntimeError("probe")),
    )
    result = selection_funnel_graph_node({
        "question": "选品",
        "session_id": "s1",
        "funnel_context": {"category": "鞋"},
    })
    assert result["final_answer"]
    assert result["funnel_context"] == {"category": "鞋"}
