"""selection_funnel/stages/ranker.py — 漏斗层六：评分排序出 Top-N

排序键：潜力分降序 → 毛利率降序 → url（保证同分确定性，两次运行结果一致）。
推荐理由为规则拼接（P0 无 LLM）：分数维度 + 利润数字 + 数据缺口，全部可溯源。
"""
from __future__ import annotations

from backend.selection_funnel.graph_state import (
    FUNNEL_RANK,
    STAGE_RANK,
)
from backend.selection_funnel.graph_state import load_brief


def rank_candidates(candidates: list[dict], top_n: int) -> list[dict]:
    ordered = sorted(
        candidates,
        key=lambda c: (-(c.get("score") or {}).get("total", 0.0),
                       -((c.get("economics") or {}).get("margin")
                         if (c.get("economics") or {}).get("margin") is not None
                         else 0.0),
                       c.get("url") or ""),
    )
    ranked = ordered[:top_n]
    for i, item in enumerate(ranked, start=1):
        item["rank"] = i
    return ranked


def build_reason(item: dict) -> str:
    score = item.get("score") or {}
    econ = item.get("economics") or {}
    breakdown = score.get("breakdown") or {}
    parts = [
        f"潜力分 {score.get('total')}"
        f"（口碑 {breakdown.get('reputation', '-')} / 热度 {breakdown.get('heat', '-')} / "
        f"价格 {breakdown.get('price', '-')} / 差异 {breakdown.get('differentiation', '-')} / "
        f"稳定 {breakdown.get('stability', '-')}）"
    ]
    if econ.get("margin") is not None:
        parts.append(
            f"贡献利润率 {econ['margin']:.1%}（售价 {econ['price']}，净利 {econ['net_profit']}/件）")
    if item.get("pain_points"):
        parts.append("；".join(item["pain_points"]))
    if score.get("notes"):
        labels = {"data_insufficient": "部分字段缺失",
                  "single_item_pool": "缺少同类对比",
                  "insufficient_history": "历史快照不足"}
        parts.append("数据缺口: " + "、".join(labels.get(n, n) for n in score["notes"]))
    dq = item.get("data_quality") or {}
    if dq.get("completeness") is not None and dq["completeness"] < 1:
        labels = {"title": "标题", "price": "售价", "rating": "评分",
                  "review_count": "评价数", "sales": "销量", "highlights": "卖点"}
        missing = "、".join(labels.get(f, f) for f in (dq.get("missing") or []))
        parts.append(f"数据完整度 {dq['completeness']:.0%}（缺: {missing}）")
    return "；".join(parts)


def rank_node(state: dict) -> dict:
    from backend.config.selection_funnel import SELECTION_FUNNEL_TOP_N
    from backend.selection_funnel.graph_state import load_brief

    brief = load_brief(state)
    top_n = int(brief.top_n or SELECTION_FUNNEL_TOP_N)
    ranked = rank_candidates(list(state.get("candidates") or []), top_n)
    for item in ranked:
        item["reason"] = build_reason(item)

    logs = list(state.get("stage_logs") or [])
    total = len(state.get("candidates") or [])
    logs.append({"stage": STAGE_RANK, "kept": len(ranked),
                 "dropped": total - len(ranked), "reasons": [], "notes": []})
    return {
        "candidates": ranked, "stage_logs": logs,
        "status": "ok", "finished": False,
    }
