"""selector 故障时禁止按路由分数自动执行首候选。"""

from backend.orchestration.graph.tool_selector import (
    _clarify_selection,
    _router_top_candidate_fallback,
)


def _state(top=0.425, second=0.30):
    cands = [{"name": "rag.search", "score": top}]
    if second is not None:
        cands.append({"name": "web.search", "score": second})
    return {
        "query": "出差住宿费一类城市的限额是多少？",
        "route_decision": {"candidates": cands},
    }


def test_decisive_scores_do_not_execute_top_candidate():
    out = _router_top_candidate_fallback(_state(), "selector_budget_exhausted")
    assert out is None


def test_ambiguous_margin_keeps_clarify():
    out = _router_top_candidate_fallback(_state(top=0.42, second=0.40), "llm_failed")
    assert out is None
    # 调用方行为契约不变：clarify 结构原样
    blocked = _clarify_selection(_state(), "llm_failed", ["rag.search", "web.search"])
    assert blocked["selection_blocked"] is True
    assert blocked["_tool_selection"]["next_action"] == "clarify"


def test_low_top_score_keeps_clarify():
    out = _router_top_candidate_fallback(_state(top=0.30, second=0.20), "llm_failed")
    assert out is None


def test_single_candidate_high_score_does_not_fall_back():
    """单候选 + 高分（无次选）→ 仍不能自动执行。"""
    out = _router_top_candidate_fallback(_state(top=0.9, second=None), "llm_failed")
    assert out is None
