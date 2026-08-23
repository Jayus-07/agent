"""selection/scoring.py — 产品潜力规则评分（spec §5.1）

纯函数、无 I/O：输入快照 dict，输出 {total, breakdown, notes}。
五维度各归一化到 0-100，加权求和；边界场景走中性分 50 并记录 notes。

维度:
  reputation      口碑分   rating 线性映射
  heat            热度分   评价量级 + 评价增速
  price           价格竞争力 折扣力度 + 池内分位反向
  differentiation 卖点差异度 卖点关键词 Jaccard 重合率反向
  stability       稳定性   价格变异系数反向 + 有货率
"""
import bisect
import math
import statistics
from datetime import datetime
from typing import Any, Optional

DEFAULT_WEIGHTS: dict[str, float] = {
    "reputation": 0.25,
    "heat": 0.25,
    "price": 0.20,
    "differentiation": 0.15,
    "stability": 0.15,
}

_NEUTRAL = 50.0


def split_keywords(highlights: str) -> set[str]:
    """卖点字符串 → 关键词集合（兼容中英文逗号/分号）"""
    if not highlights:
        return set()
    parts = highlights.replace("，", ",").replace("；", ",").replace(";", ",").split(",")
    return {p.strip() for p in parts if p.strip()}


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


# ── 各维度评分（返回 (分数, note 或 None)）──────────────

def _reputation(latest: dict) -> tuple[float, Optional[str]]:
    rating = latest.get("rating")
    if rating is None:
        return _NEUTRAL, "data_insufficient"
    return _clip01((rating - 4.0) / 0.8) * 100, None


def _review_growth_per_day(history: list[dict]) -> Optional[float]:
    """最近两条含评价数的快照间的日增速（history 新→旧）"""
    pts = [
        (s.get("crawled_at"), s.get("review_count"))
        for s in history
        if s.get("review_count") is not None and s.get("crawled_at")
    ]
    if len(pts) < 2:
        return None
    try:
        t_new = datetime.fromisoformat(pts[0][0])
        t_old = datetime.fromisoformat(pts[1][0])
    except ValueError:
        return None
    days = (t_new - t_old).total_seconds() / 86400
    if days <= 0:
        return None
    return (pts[0][1] - pts[1][1]) / days


def _heat(latest: dict, history: list[dict],
          pool_latest: list[dict]) -> tuple[float, Optional[str]]:
    rc = latest.get("review_count")
    if rc is None:
        return _NEUTRAL, "data_insufficient"
    rcs = [s["review_count"] for s in pool_latest if s.get("review_count") is not None]
    max_rc = max(rcs) if rcs else rc
    if max_rc > 0:
        magnitude = math.log10(rc + 1) / math.log10(max_rc + 1) * 100
    else:
        magnitude = _NEUTRAL
    growth = _review_growth_per_day(history)
    if growth is None:
        growth_score = _NEUTRAL
    else:
        # 饱和归一：日增 20 条 ≈ 50 分，日增 180 条 ≈ 90 分
        growth_score = 100 * max(growth, 0.0) / (max(growth, 0.0) + 20.0)
    return 0.7 * magnitude + 0.3 * growth_score, None


def _price(latest: dict, pool_latest: list[dict]) -> tuple[float, Optional[str]]:
    price = latest.get("price")
    if price is None:
        return _NEUTRAL, "data_insufficient"
    # 折扣力度：40% 折扣即满分
    orig = latest.get("original_price")
    if orig and orig > price:
        discount = _clip01((orig - price) / orig * 2.5) * 100
    else:
        discount = _NEUTRAL
    # 池内价格分位反向（越便宜分越高）
    prices = sorted(s["price"] for s in pool_latest if s.get("price") is not None)
    if len(prices) < 2:
        return 0.5 * discount + 0.5 * _NEUTRAL, "single_item_pool"
    rank = bisect.bisect_left(prices, price)
    quantile_rev = (1 - rank / (len(prices) - 1)) * 100
    return 0.5 * discount + 0.5 * quantile_rev, None


def _differentiation(latest: dict, pool_latest: list[dict]) -> tuple[float, Optional[str]]:
    kws = split_keywords(latest.get("highlights") or "")
    others = [
        split_keywords(s.get("highlights") or "")
        for s in pool_latest
        if s.get("url") != latest.get("url")
    ]
    others = [o for o in others if o]
    if not others:
        return _NEUTRAL, "single_item_pool"
    if not kws:
        return _NEUTRAL, "data_insufficient"
    overlaps = [len(kws & o) / len(kws | o) for o in others]
    return (1 - sum(overlaps) / len(overlaps)) * 100, None


def _stability(history: list[dict]) -> tuple[float, Optional[str]]:
    if len(history) < 2:
        return _NEUTRAL, "insufficient_history"
    priced = [s["price"] for s in history if s.get("price") is not None]
    if len(priced) < 2:
        return _NEUTRAL, "insufficient_history"
    mean = statistics.mean(priced)
    cv = statistics.pstdev(priced) / mean if mean else 0.0
    cv_score = max(0.0, 1 - cv * 5) * 100  # 变异系数 ≥20% → 0 分
    stock_rate = sum(1 for s in history if s.get("in_stock")) / len(history) * 100
    return 0.5 * cv_score + 0.5 * stock_rate, None


# ── 主入口 ──────────────────────────────────────

def score_product(
    latest: dict[str, Any],
    history: list[dict[str, Any]],
    pool_latest: list[dict[str, Any]],
    weights: Optional[dict[str, float]] = None,
) -> dict[str, Any]:
    """对单个商品计算潜力分。

    参数:
        latest:      该商品最新快照
        history:     该商品历史快照（新→旧，来自 CompetitorStore.history）
        pool_latest: 候选池内全部商品的最新快照（含自身）
        weights:     权重（None = 默认；和 ≠ 1 时自动归一化）

    返回: {"total": float, "breakdown": {dim: score}, "notes": [str]}
    """
    w = dict(weights or DEFAULT_WEIGHTS)
    total_w = sum(w.values())
    if total_w <= 0:
        w = dict(DEFAULT_WEIGHTS)
        total_w = 1.0

    dims = {
        "reputation": _reputation(latest),
        "heat": _heat(latest, history, pool_latest),
        "price": _price(latest, pool_latest),
        "differentiation": _differentiation(latest, pool_latest),
        "stability": _stability(history),
    }

    breakdown = {k: round(v[0], 1) for k, v in dims.items()}
    notes: list[str] = []
    for _, note in dims.values():
        if note and note not in notes:
            notes.append(note)

    total = sum(w[k] / total_w * v[0] for k, v in dims.items())
    return {"total": round(total, 1), "breakdown": breakdown, "notes": notes}
