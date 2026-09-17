"""selection_funnel/stages/screener.py — 漏斗层三：指标初筛

硬阈值淘汰（rating / review_count / 价格带复核），阈值只读 config；
**缺数据的候选保留并记 notes**——漏斗淘汰必须「拿正证据」，
数据不足不是淘汰理由，是披露理由（reporter 会如实说明）。
"""
from __future__ import annotations

from backend.selection_funnel.graph_state import (
    FUNNEL_SCREEN,
    STAGE_SCREEN,
    STATUS_EMPTY,
)
from backend.selection_funnel.graph_state import load_brief


def screen_candidates(candidates: list[dict], category: str,
                      min_rating: float, min_reviews: int,
                      price_min: float | None = None,
                      price_max: float | None = None,
                      ) -> tuple[list[dict], list[dict], list[str]]:
    """Returns (kept, reasons, notes)。kept 内每项附 screening dict。"""
    kept: list[dict] = []
    reasons: list[dict] = []
    notes: list[str] = []
    for c in candidates:
        rating = c.get("rating")
        if rating is not None and rating < min_rating:
            reasons.append({"url": c.get("url", ""), "title": c.get("title") or "",
                            "rule": "min_rating", "value": rating})
            continue
        reviews = c.get("review_count")
        heat = reviews
        if heat is None and c.get("sales") is not None:
            # 榜单数据常有销量没评价数：以销量代热度线（口径披露，不静默）
            heat = c.get("sales")
        if heat is not None and heat < min_reviews:
            reasons.append({"url": c.get("url", ""), "title": c.get("title") or "",
                            "rule": "min_reviews", "value": heat})
            continue
        price = c.get("price")
        if price is not None and price_min is not None and price < price_min:
            reasons.append({"url": c.get("url", ""), "title": c.get("title") or "",
                            "rule": "price_below_band", "value": price})
            continue
        if price is not None and price_max is not None and price > price_max:
            reasons.append({"url": c.get("url", ""), "title": c.get("title") or "",
                            "rule": "price_above_band", "value": price})
            continue
        item = dict(c)
        warn = []
        if rating is None:
            warn.append("rating 缺失")
        if reviews is None and c.get("sales") is not None:
            warn.append(f"review_count 缺失，热度线按销量 {c.get('sales'):g} 代判")
        elif reviews is None:
            warn.append("review_count 缺失")
        item["screening"] = {"passed": True, "warnings": warn}
        if warn:
            notes.append(f"{(c.get('title') or c.get('url', ''))[:30]}: "
                         f"{'、'.join(warn)}（数据不足，保留待验证层披露）")
        kept.append(item)
    return kept, reasons, notes


def screen_node(state: dict) -> dict:
    """漏斗节点：初筛。全淘汰 → empty_pool 短路。"""
    from backend.config.selection_funnel import (
        SELECTION_FUNNEL_MIN_RATING,
        SELECTION_FUNNEL_MIN_REVIEWS,
        rules_for,
    )
    from backend.selection_funnel.graph_state import load_brief
    from backend.selection_funnel.reporter import render_empty_pool

    brief = load_brief(state)
    rules = rules_for(brief.category)
    min_rating = float(rules.get("min_rating", SELECTION_FUNNEL_MIN_RATING))
    min_reviews = int(rules.get("min_reviews", SELECTION_FUNNEL_MIN_REVIEWS))

    candidates = list(state.get("candidates") or [])
    kept, reasons, notes = screen_candidates(
        candidates, brief.category, min_rating, min_reviews,
        brief.price_min, brief.price_max)

    logs = list(state.get("stage_logs") or [])
    notes_all = list(state.get("notes") or []) + notes
    logs.append({"stage": STAGE_SCREEN, "kept": len(kept),
                 "dropped": len(candidates) - len(kept),
                 "reasons": reasons[:50], "notes": notes})

    if not kept:
        return {
            "candidates": [], "stage_logs": logs, "notes": notes_all,
            "status": STATUS_EMPTY,
            "final_answer": render_empty_pool(brief, logs, notes_all),
            "finished": False,
        }
    return {"candidates": kept, "stage_logs": logs,
            "notes": notes_all, "status": "ok", "finished": False}
