"""selection_funnel/stages/verifier.py — 漏斗层四：竞品验证

复用 backend/selection/scoring.py 的五维潜力分（口碑/热度/价格/差异度/稳定），
**不造第二套评分**。本层不淘汰——只把分数、分维度、数据缺口挂到候选上；
淘汰交给显式阈值层（screen/econ），保证违反率统计不失真。

差评痛点（六层漏斗第 4 层的「差评找卖点」）：P0 数据源只有快照
highlights/promo_text，真正的评论抓取属待办批次 5（review_pain 升级），
就绪后在 extract_pain_points 替换实现，接口不变。
"""
from __future__ import annotations

from typing import Any

from backend.selection.scoring import DEFAULT_WEIGHTS, score_product
from backend.shared.logger import logger

_NOTE_LABELS = {
    "data_insufficient": "部分字段缺失",
    "single_item_pool": "候选池内缺少同类对比",
    "insufficient_history": "历史快照不足",
}


def _default_store() -> Any:
    from backend.competitor.store import get_store
    return get_store()


def verify_candidates(candidates: list[dict],
                      store: Any = None) -> tuple[list[dict], list[str]]:
    """给每个候选挂 score / pain_points。Returns (enriched, notes)。"""
    store = store or _default_store()
    pool_latest = candidates  # 评分池 = 当前存活候选（与 scoring 口径一致）
    notes: list[str] = []
    enriched: list[dict] = []
    for c in candidates:
        url = c.get("url") or ""
        try:
            history = store.history(url, limit=50)
        except Exception as e:
            logger.warning("[FunnelVerify] 历史快照读取失败 url=%s: %s", url, e)
            history = []
            notes.append(f"{(c.get('title') or url)[:30]}: 历史快照读取失败，稳定性维度按中性分计")
        score = score_product(c, history, pool_latest, DEFAULT_WEIGHTS)
        item = dict(c)
        item["score"] = score
        item["pain_points"] = extract_pain_points(c)
        enriched.append(item)
    return enriched, notes


def extract_pain_points(candidate: dict) -> list[str]:
    """P0：从 promo_text/highlights 的促销内卷信号提炼差异化机会。

    例：促销文案密集 → 该品靠价格战，差异化空间 = 品质/服务维度。
    评论级痛点分析等批次 5 数据源就绪后替换本实现。
    """
    pains: list[str] = []
    promo = str(candidate.get("promo_text") or "")
    if any(k in promo for k in ("券", "折", "减", "秒杀", "满减", "包邮")):
        pains.append("同池促销依赖度高：价格战竞争，差异化应避开纯价格维度")
    highlights = str(candidate.get("highlights") or "")
    if not highlights:
        pains.append("卖点信息缺失：需人工补看详情页再下结论")
    return pains


def verify_node(state: dict) -> dict:
    enriched, notes = verify_candidates(list(state.get("candidates") or []))
    logs = list(state.get("stage_logs") or [])
    logs.append({"stage": "verify", "kept": len(enriched), "dropped": 0,
                 "reasons": [], "notes": notes})
    return {
        "candidates": enriched,
        "stage_logs": logs,
        "notes": list(state.get("notes") or []) + notes,
        "status": "ok", "finished": False,
    }
