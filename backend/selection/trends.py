"""selection/trends.py — 结构趋势聚合（spec §4.2）

直接对 competitor_snapshots 聚合，数字不经 LLM：
  - price_quantiles: 按天的价格 p25/p50/p75
  - review_growth:   每个 URL 的评价数日增速
  - highlight_freq:  卖点关键词词频
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Optional

from backend.selection.scoring import split_keywords


def compute_trends(
    store,
    days: int = 30,
    platform: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """从 CompetitorStore 聚合趋势数据。

    参数:
        store:   CompetitorStore 实例
        days:    最近 N 天（0 = 全部）
        platform: 平台过滤（None = 全部）
        now_iso: 测试注入的"当前时间"（ISO 字符串）
    """
    now = datetime.fromisoformat(now_iso) if now_iso else datetime.now()

    # 全量读快照（watchlist 规模下内存聚合足够；量大后迁移 SQL 窗口函数）
    rows = store.list_snapshots()

    if days > 0:
        cutoff = (now - timedelta(days=days)).isoformat()
        rows = [r for r in rows if (r.get("crawled_at") or "") >= cutoff]
    if platform:
        rows = [r for r in rows if r.get("platform") == platform]

    # ── 价格分位数（按天）──
    by_day: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r.get("price") is not None and r.get("crawled_at"):
            by_day[r["crawled_at"][:10]].append(r["price"])
    price_quantiles = []
    for date in sorted(by_day):
        prices = sorted(by_day[date])
        price_quantiles.append({
            "date": date,
            "p25": _quantile(prices, 0.25),
            "p50": _quantile(prices, 0.50),
            "p75": _quantile(prices, 0.75),
        })

    # ── 评价增速（每 URL 最近两条含评价数的快照）──
    by_url: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_url[r["url"]].append(r)
    review_growth = []
    watch_names = {w["url"]: w["name"] for w in store.list_watch(enabled_only=False)}
    for url, snaps in by_url.items():
        pts = []
        for s in snaps:
            if s.get("review_count") is None or not s.get("crawled_at"):
                continue
            try:
                datetime.fromisoformat(s["crawled_at"])
            except ValueError:
                continue  # 时间戳不可解析则跳过该数据点
            pts.append(s)
        if len(pts) < 2:
            continue
        old, new = pts[-2], pts[-1]
        span_days = (datetime.fromisoformat(new["crawled_at"])
                     - datetime.fromisoformat(old["crawled_at"])).total_seconds() / 86400
        if span_days <= 0:
            continue
        review_growth.append({
            "url": url,
            "name": watch_names.get(url) or new.get("title") or url,
            "daily_delta": round((new["review_count"] - old["review_count"]) / span_days, 1),
        })
    review_growth.sort(key=lambda g: g["daily_delta"], reverse=True)

    # ── 卖点词频（过滤后的每条快照各计一次，反映窗口内卖点的历史出现频次）──
    freq: Counter = Counter()
    for r in rows:
        if r.get("highlights"):
            freq.update(split_keywords(r["highlights"]))
    highlight_freq = [{"keyword": k, "count": c} for k, c in freq.most_common(20)]

    # ── 商品条目（供前端表格）──
    items = []
    for url, snaps in by_url.items():
        latest = snaps[-1]
        items.append({
            "url": url,
            "name": watch_names.get(url) or latest.get("title") or url,
            "platform": latest.get("platform") or "generic",
            "latest_price": latest.get("price"),
            "rating": latest.get("rating"),
            "review_count": latest.get("review_count"),
            "highlights": latest.get("highlights") or "",
            "latest_crawled_at": latest.get("crawled_at"),
        })

    return {
        "days": days,
        "platform": platform,
        "items": items,
        "price_quantiles": price_quantiles,
        "review_growth": review_growth,
        "highlight_freq": highlight_freq,
        "sources": {"snapshot_count": len(rows), "rag_hits": 0},
    }


def _quantile(sorted_vals: list[float], q: float) -> float:
    """简单线性插值分位数（sorted_vals 非空）"""
    if len(sorted_vals) == 1:
        return round(sorted_vals[0], 2)
    idx = q * (len(sorted_vals) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return round(sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac, 2)
