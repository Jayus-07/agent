"""TD-06 selector 故障时的路由首候选兜底回归（2026-10-02）。

实测背景：路由给出 rag.search 0.425 / web.search 0.30，selector LLM
12.4s 超出 10.5s 预算 → clarify → direct_executor 阻断 → 整问 25.8s
失败（「未能找到相关信息」）。路由分数分布本可决策，selector 基础设施
故障不应否决之。钉死四个行为：
  1. top ≥ 0.4 且领先次选 ≥ 0.1 → 兜底执行首候选（FC 成功同构状态）；
  2. 分数接近（margin < 0.1）→ 不兜底，维持 clarify；
  3. top 分数低于 floor → 不兜底，维持 clarify；
  4. floor=0 → 兜底关闭，回旧行为。
"""
import pytest

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


def test_decisive_scores_execute_top_candidate():
    out = _router_top_candidate_fallback(_state(), "selector_budget_exhausted")
    assert out is not None
    assert out["selected_tool"] == "rag.search"
    assert out["resolved_params"] == {"question": "出差住宿费一类城市的限额是多少？"}
    assert out["_tool_selection"]["source"] == "router_top_candidate"
    assert out["_tool_selection"]["top_score"] == 0.425
    # 路由候选保持原序（首候选已在其位，不虚构 0.95 分）
    assert out["route_decision"]["candidates"][0]["name"] == "rag.search"
    assert "selection_blocked" not in out  # 兜底路径不进入阻断态


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


def test_floor_zero_disables_fallback(monkeypatch):
    import backend.config as config

    monkeypatch.setattr(config, "TOOL_SELECTOR_TOP_CANDIDATE_FLOOR", 0.0)
    out = _router_top_candidate_fallback(_state(), "llm_failed")
    assert out is None


def test_single_candidate_high_score_falls_back():
    """单候选 + 高分（无次选，margin=top）→ 兜底执行而非 passthrough。"""
    out = _router_top_candidate_fallback(_state(top=0.9, second=None), "llm_failed")
    assert out is not None
    assert out["selected_tool"] == "rag.search"
