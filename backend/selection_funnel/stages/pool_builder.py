"""selection_funnel/stages/pool_builder.py — 漏斗层二：拉词建池（多源）

数据源（2026-09-17 用户拍板，SELECTION_FUNNEL_POOL_SOURCES 可配顺序）：
  1. import    — 批量导入通道（生意参谋/竞品工具导出表格 → import_pool.py），
                 海选主源。watchlist 受反爬预算约束（GLOBAL_DAILY_BUDGET=40 次/天）
                 只能养 1-2 个对象，撑不起漏斗的海选语义，降级为兜底补充源。
  2. watchlist — 竞品监控快照池（backend/competitor.store）。

本域不依赖外站爬取——旧 selection_decision 工作流跑不通的根因就是
「需要真实竞品 URL + 抓取（外站反爬不确定）」。

类目匹配口径（保守，宁缺毋滥误收）：
  候选 category 字段命中，或 title / highlights 含类目词。
  一个都匹配不上 → 空池如实报告，绝不把不相关候选硬塞进漏斗。

多源合并口径：url 相同去重（前源优先），无 url 时按 (title, platform)；
单源读取失败降级为跳过该源 + note，不炸漏斗。
"""
from __future__ import annotations

from typing import Any

from backend.selection_funnel.graph_state import (
    FUNNEL_POOL,
    STAGE_POOL,
    STATUS_EMPTY,
)
from backend.shared.logger import logger

# 快照 → 漏斗候选的归一化字段（缺字段不补造，保留 None 由下游标注）
# unit_cost：候选级成本（导入表「成本」列），优先于需求级 brief.max_unit_cost
# imported_at：导入行时间戳（watchlist 走 crawled_at），供数据新鲜度披露
_POOL_FIELDS = (
    "url", "title", "platform", "price", "original_price", "currency",
    "rating", "review_count", "sales", "unit_cost", "highlights",
    "promo_text", "in_stock", "category", "crawled_at", "imported_at",
    "snapshot_id",
)


def _default_store() -> Any:
    """惰性取竞品监控 store（测试 monkeypatch 本函数隔离真实库）。"""
    from backend.competitor.store import get_store
    return get_store()


def _load_import_candidates() -> tuple[list[dict], list[str]]:
    """源一：批量导入候选池。失败降级为跳过 + note。"""
    notes: list[str] = []
    try:
        from backend.selection_funnel.import_pool import get_import_store
        items = get_import_store().list_candidates()
    except Exception as e:
        logger.warning("[FunnelPool] 导入候选池读取失败: %s", e)
        return [], [f"导入候选池读取失败，已跳过该源: {e}"]
    if not items:
        notes.append("导入候选池为空——可上传生意参谋/竞品工具导出的表格（CSV/Excel/粘贴）建海选池。")
    return items, notes


def _load_watchlist_candidates(store: Any) -> tuple[list[dict], list[str]]:
    """源二：竞品监控快照池。失败降级为跳过 + note。"""
    notes: list[str] = []
    try:
        items = store.list_watch(enabled_only=True)
    except Exception as e:
        logger.warning("[FunnelPool] 候选源读取失败: %s", e)
        return [], [f"竞品监控池读取失败，已跳过该源: {e}"]
    out: list[dict] = []
    for item in items:
        url = item.get("url") or ""
        snap = store.latest_snapshot(url)
        if not snap:
            continue
        out.append({k: snap.get(k) for k in _POOL_FIELDS} | {"url": url})
    return out, notes


def _filter_one(cand: dict, category: str, platform: str,
                price_min: float | None, price_max: float | None) -> str | None:
    """单项过滤：返回剔除 rule 名，通过返回 None。"""
    if not _match_category(cand, category):
        return "category_mismatch"
    if platform and (cand.get("platform") or "") != platform:
        return "platform_mismatch"
    price = cand.get("price")
    if price is not None and price_min is not None and price < price_min:
        return "price_below_band"
    if price is not None and price_max is not None and price > price_max:
        return "price_above_band"
    return None


def _dedup_key(cand: dict) -> tuple[str, str]:
    url = (cand.get("url") or "").strip()
    return ("url", url) if url else ("title", f"{(cand.get('title') or '').strip()}|{cand.get('platform') or ''}")


def build_pool(category: str, platform: str = "",
               price_min: float | None = None,
               price_max: float | None = None,
               max_pool: int = 200,
               store: Any = None) -> tuple[list[dict], list[str], list[dict]]:
    """构建候选池（多源合并 → 统一过滤 → 去重 → 截断）。

    Returns:
        (pool, notes, reasons) —— reasons 供 stage_logs 记录剔除明细。
    """
    from backend.config.selection_funnel import SELECTION_FUNNEL_POOL_SOURCES

    notes: list[str] = []
    reasons: list[dict] = []

    # 1) 按 POOL_SOURCES 顺序收集（前源优先）
    collected: list[dict] = []
    for source in SELECTION_FUNNEL_POOL_SOURCES:
        if source == "import":
            items, src_notes = _load_import_candidates()
        elif source == "watchlist":
            items, src_notes = _load_watchlist_candidates(store or _default_store())
        else:
            items, src_notes = [], [f"未知数据源「{source}」已跳过（支持: import, watchlist）"]
        notes.extend(src_notes)
        collected.extend(items)

    # 2) 统一过滤 + 去重 + 截断
    pool: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for cand in collected:
        rule = _filter_one(cand, category, platform, price_min, price_max)
        if rule:
            reasons.append({
                "url": cand.get("url") or "", "title": cand.get("title") or cand.get("url") or "",
                "rule": rule, "value": cand.get("price") if rule.startswith("price_") else "",
            })
            continue
        key = _dedup_key(cand)
        if key in seen:
            reasons.append({
                "url": cand.get("url") or "", "title": cand.get("title") or "",
                "rule": "duplicate", "value": "",
            })
            continue
        seen.add(key)
        pool.append({k: cand.get(k) for k in _POOL_FIELDS} | {"url": cand.get("url") or ""})
        if len(pool) >= max_pool:
            notes.append(f"候选池达到上限 {max_pool}，超出部分未纳入（放宽 SELECTION_FUNNEL_MAX_POOL 可调整）")
            break

    if not pool:
        notes.append(
            f"候选池中没有命中类目「{category}」的商品。"
            "可上传同类目竞品表格建导入池（主源），或把核心竞品加入监控（/competitor 接口，兜底）后再跑。")
    if pool and not any(c.get("category") for c in pool):
        notes.append("候选缺 category 字段，本次按 title/highlights 关键词匹配类目；"
                     "导入表格加「类目」列后匹配更准。")
    return pool, notes, reasons


def _match_category(snap: dict, category: str) -> bool:
    if not category:
        return True
    hay = " ".join(filter(None, (
        str(snap.get("category") or ""),
        str(snap.get("title") or ""),
        str(snap.get("highlights") or ""),
    )))
    return category in hay


def pool_node(state: dict) -> dict:
    """漏斗节点：建池。池空 → empty_pool 短路进 reporter。"""
    from backend.config.selection_funnel import SELECTION_FUNNEL_MAX_POOL
    from backend.selection_funnel.graph_state import load_brief

    brief = load_brief(state)
    pool, notes, reasons = build_pool(
        category=brief.category, platform=brief.platform,
        price_min=brief.price_min, price_max=brief.price_max,
        max_pool=SELECTION_FUNNEL_MAX_POOL,
    )
    logs = list(state.get("stage_logs") or [])
    notes_all = list(state.get("notes") or []) + notes
    logs.append({"stage": "pool", "kept": len(pool), "dropped": len(reasons),
                 "reasons": reasons[:50], "notes": notes})

    if not pool:
        from backend.selection_funnel.reporter import render_empty_pool
        return {
            FUNNEL_POOL + "_done": True,
            "pool": [], "candidates": [],
            "stage_logs": logs, "notes": notes_all,
            "status": STATUS_EMPTY,
            "final_answer": render_empty_pool(brief, logs, notes_all),
            "finished": False,
        }
    return {
        "pool": pool, "candidates": pool,
        "stage_logs": logs, "notes": notes_all,
        "status": "ok", "finished": False,
    }
