"""selection_funnel/models/funnel_result.py — 漏斗域图 → 主图契约

与 travel.models.graph_result 同职责：把域图终态收敛成稳定 dict，
适配器只透传，不让主图感知域内状态细节。
"""
from __future__ import annotations

from typing import Any


def build_funnel_result(final_state: dict) -> dict[str, Any]:
    """终态 → {final_answer, funnel_context, status}。

    funnel_context 只放轻量摘要（推荐条目 + 各层计数），
    不携带整份候选池，避免主状态/checkpointer 反复序列化大对象。
    """
    stage_logs = final_state.get("stage_logs") or []
    stage_summary = [
        {"stage": log.get("stage", ""), "kept": log.get("kept", 0),
         "dropped": log.get("dropped", 0)}
        for log in stage_logs
    ]
    # pool 层附来源健康度（P2）：trace funnel_stage_summary 可见，
    # 管理端运行历史页后续可据此聚合跨运行健康趋势
    for log in stage_logs:
        if log.get("stage") == "pool" and log.get("sources"):
            for entry in stage_summary:
                if entry["stage"] == "pool":
                    entry["sources"] = log["sources"]
    top = [
        {
            "rank": c.get("rank"),
            "title": c.get("title") or c.get("url", ""),
            "url": c.get("url", ""),
            "platform": c.get("platform") or "",
            "price": c.get("price"),
            # rating/review_count/highlights：selection_decision 衔接（选项 A）所需
            # ——证据指标用评价数、痛点材料用卖点；仍是 Top-N 轻量摘要不携带整池
            "rating": c.get("rating"),
            "review_count": c.get("review_count"),
            "highlights": c.get("highlights") or "",
            "score_total": (c.get("score") or {}).get("total"),
            "margin": (c.get("economics") or {}).get("margin"),
            "gross_margin": (c.get("economics") or {}).get("gross_margin"),
            "completeness": (c.get("data_quality") or {}).get("completeness"),
            "freshness": (c.get("data_quality") or {}).get("freshness"),
        }
        for c in (final_state.get("candidates") or [])
    ]
    brief = final_state.get("brief") or {}
    context: dict[str, Any] = {
        "category": brief.get("category", ""),
        "status": final_state.get("status") or "ok",
        "top": top,
        "stage_summary": stage_summary,
    }
    # 运行配置快照（reporter 正常路径产出）→ 透传给 trace metadata
    if final_state.get("config_snapshot"):
        context["config_snapshot"] = final_state["config_snapshot"]
    return {
        "final_answer": final_state.get("final_answer") or "",
        "status": final_state.get("status") or "ok",
        "funnel_context": context,
    }
