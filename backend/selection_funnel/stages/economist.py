"""selection_funnel/stages/economist.py — 漏斗层五：利润测算淘汰

毛利率 = (售价 − 成本 − 平台扣点 − 物流 − 推广费) / 售价，
低于目标线直接淘汰并给出完整数字明细 —— 淘汰理由必须可复算。
"""
from __future__ import annotations

from backend.selection_funnel.economics import calc_unit_economics
from backend.selection_funnel.graph_state import (
    FUNNEL_ECON,
    STAGE_ECON,
    STATUS_EMPTY,
)
from backend.selection_funnel.graph_state import load_brief


def econ_candidates(candidates: list[dict], category: str,
                    min_margin: float, unit_cost: float | None,
                    fee_rate: float, logistics_fee: float,
                    ads_ratio: float, refund_ratio: float,
                    default_cost_ratio: float,
                    ) -> tuple[list[dict], list[dict], list[str]]:
    """Returns (kept, reasons, notes)。kept 每项附 economics dict。"""
    kept: list[dict] = []
    reasons: list[dict] = []
    notes: list[str] = []
    for c in candidates:
        econ = calc_unit_economics(
            price=c.get("price"), unit_cost=unit_cost,
            fee_rate=fee_rate, logistics_fee=logistics_fee,
            ads_ratio=ads_ratio, refund_ratio=refund_ratio,
            default_cost_ratio=default_cost_ratio,
        )
        item = dict(c)
        item["economics"] = econ
        if econ["margin"] is None:
            notes.append(f"{(c.get('title') or c.get('url', ''))[:30]}: "
                         f"{'; '.join(econ['warnings'])}（无利润数据，保留并在报告披露）")
            kept.append(item)
            continue
        if econ["margin"] < min_margin:
            reasons.append({
                "url": c.get("url", ""), "title": c.get("title") or "",
                "rule": "min_margin",
                "value": f"毛利率 {econ['margin']:.1%} < 目标 {min_margin:.0%} "
                         f"(售价 {econ['price']} − 成本 {econ['unit_cost']} "
                         f"− 扣点 {econ['platform_fee']} − 物流 {econ['logistics_fee']} "
                         f"− 推广 {econ['ads_fee']} − 退款损耗 {econ['refund_loss']})",
            })
            continue
        kept.append(item)
    return kept, reasons, notes


def econ_node(state: dict) -> dict:
    from backend.config.selection_funnel import (
        SELECTION_FUNNEL_ADS_RATIO,
        SELECTION_FUNNEL_DEFAULT_COST_RATIO,
        SELECTION_FUNNEL_LOGISTICS_FEE_CNY,
        SELECTION_FUNNEL_MIN_MARGIN,
        SELECTION_FUNNEL_PLATFORM_FEE_RATE,
        SELECTION_FUNNEL_REFUND_RATIO,
        rules_for,
    )
    from backend.selection_funnel.graph_state import load_brief
    from backend.selection_funnel.reporter import render_empty_pool

    brief = load_brief(state)
    rules = rules_for(brief.category)
    min_margin = float(brief.target_margin
                       or rules.get("min_margin", SELECTION_FUNNEL_MIN_MARGIN))

    candidates = list(state.get("candidates") or [])
    kept, reasons, notes = econ_candidates(
        candidates, brief.category, min_margin, brief.max_unit_cost,
        fee_rate=float(rules.get("fee_rate", SELECTION_FUNNEL_PLATFORM_FEE_RATE)),
        logistics_fee=float(rules.get("logistics_fee", SELECTION_FUNNEL_LOGISTICS_FEE_CNY)),
        ads_ratio=float(rules.get("ads_ratio", SELECTION_FUNNEL_ADS_RATIO)),
        refund_ratio=float(rules.get("refund_ratio", SELECTION_FUNNEL_REFUND_RATIO)),
        default_cost_ratio=float(rules.get(
            "default_cost_ratio", SELECTION_FUNNEL_DEFAULT_COST_RATIO)),
    )

    logs = list(state.get("stage_logs") or [])
    notes_all = list(state.get("notes") or []) + notes
    logs.append({"stage": STAGE_ECON, "kept": len(kept),
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
