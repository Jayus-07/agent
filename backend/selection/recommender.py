"""selection/recommender.py — 选品引擎编排层（spec §2 / §5）

候选（watchlist 快照）→ 规则打分 → LLM 理由（事实锁定）→ 组装推荐结果。
供 REST 路由与后续对话 Skill 共用。
"""
from datetime import datetime
from typing import Any, Optional

from backend.competitor.store import get_store
from backend.infra.llm import llm
from backend.selection.scoring import score_product
from backend.selection.store import get_selection_store
from backend.shared.logger import logger

# 复用 llm_polisher 的数值提取（事实锁定模式，spec §5.2）
# 注：实际函数名为 _extract_numerical_tokens（计划中写作 _extract_numbers）
from backend.business_report.llm_polisher import _extract_numerical_tokens

_NOTE_LABELS = {
    "data_insufficient": "部分字段缺失",
    "single_item_pool": "候选池内缺少同类对比",
    "insufficient_history": "历史快照不足",
}


def _pool() -> tuple[list[dict], list[dict]]:
    """返回 (候选 URL 列表, 全部商品最新快照)。评分池 = 全部启用监控项。"""
    store = get_store()
    items = store.list_watch(enabled_only=True)
    pool_latest = []
    urls = []
    for item in items:
        snap = store.latest_snapshot(item["url"])
        if snap and (snap.get("price") is not None or snap.get("title")):
            pool_latest.append(snap)
            urls.append(item["url"])
    return urls, pool_latest


def _build_item(url: str, pool_latest: list[dict], weights: dict,
                use_llm: bool = True, force_refresh: bool = False) -> Optional[dict]:
    """组装单个商品的推荐条目（打分 + 缓存 + LLM 理由）"""
    store = get_store()
    sel_store = get_selection_store()

    latest = store.latest_snapshot(url)
    if not latest:
        return None

    cached = sel_store.get_score(url)
    if (not force_refresh and cached is not None
            and cached.get("snapshot_id") == latest.get("id")):
        score = cached["score_json"]
        scored_at = cached["computed_at"]
    else:
        history = store.history(url, limit=50)
        score = score_product(latest, history, pool_latest, weights)
        sel_store.save_score(url, score, latest.get("id"))
        scored_at = datetime.now().isoformat(timespec="seconds")

    item = {
        "url": url,
        "title": latest.get("title") or url,
        "platform": latest.get("platform") or "generic",
        "latest_price": latest.get("price"),
        "currency": latest.get("currency") or "CNY",
        "rating": latest.get("rating"),
        "review_count": latest.get("review_count"),
        "highlights": latest.get("highlights") or "",
        "score": score,
        "llm_reason": "",
        "llm_risks": "",
        "latest_crawled_at": latest.get("crawled_at"),
        "scored_at": scored_at,
    }
    if use_llm:
        item.update(generate_reason(item))
    return item


def _norm_num(tok: str) -> str:
    """数值 token 归一：去千分位逗号、去尾零（129.0 == 129 == 129.00）

    含非纯数字字符的 token（%/¥/日期/时间等形态）原样保留。
    """
    t = tok.replace(",", "")
    if not t.lstrip("-").replace(".", "").isdigit():
        return tok
    if "." in t:
        t = t.rstrip("0").rstrip(".")
    return t or "0"


def generate_reason(payload: dict[str, Any]) -> dict[str, str]:
    """基于打分与快照字段生成推荐理由/风险提示。

    事实锁定：LLM 输出中的数值必须可在输入数据中溯源，
    否则回退模板文案（防编造数字）。
    """
    score = payload.get("score") or {}
    fallback_reason = (
        f"潜力分 {score.get('total')}（口碑 {score.get('breakdown', {}).get('reputation')} / "
        f"热度 {score.get('breakdown', {}).get('heat')} / "
        f"价格 {score.get('breakdown', {}).get('price')}），"
        f"现价 {payload.get('latest_price')} {payload.get('currency', 'CNY')}，"
        f"评分 {payload.get('rating') if payload.get('rating') is not None else '-'}，"
        f"评价数 {payload.get('review_count') if payload.get('review_count') is not None else '-'}。"
    )
    notes = score.get("notes") or []
    fallback_risks = (
        "；".join(_NOTE_LABELS.get(n, n) for n in notes)
        if notes else "暂无明显风险信号。"
    )

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        human_content = (
            f"商品: {payload.get('title')}\n"
            f"平台: {payload.get('platform')}\n"
            f"现价: {payload.get('latest_price')} {payload.get('currency')}\n"
            f"评分: {payload.get('rating')} 评价数: {payload.get('review_count')}\n"
            f"卖点: {payload.get('highlights', '')}\n"
            f"潜力分: {score.get('total')} 分维度: {score.get('breakdown')}\n"
            f"数据缺口标注: {notes}"
        )
        messages = [
            SystemMessage(content=(
                "你是电商选品分析师。根据给定数据写 1-2 句推荐理由。"
                "严格规则：只允许使用给定数据中出现的数字，禁止编造、推算或改写任何数字。"
            )),
            HumanMessage(content=human_content),
        ]
        resp = llm.invoke(messages)
        text = resp.content.strip()
    except Exception as e:
        logger.warning(f"[Recommender] LLM 理由生成失败，回退模板: {e}")
        return {"llm_reason": fallback_reason,
                "llm_risks": f"LLM 理由生成失败，以下为规则摘要。{fallback_risks}"}

    # 事实锁定校验：输出中出现的每个数字必须可在输入数据中溯源（禁止编造）。
    # 注意与 llm_polisher 方向相反：润色要求保留全部事实，理由生成只写 1-2 句，
    # 因此校验"输出 ⊆ 输入"而非"输入 ⊆ 输出"。
    # 白名单 = source_facts（分数/价格/评分等）+ human_content（title/highlights/platform），
    # 与 HumanMessage 实际给出的字段保持一致。
    source_facts = (
        f"潜力分 {score.get('total')} 现价 {payload.get('latest_price')} "
        f"评分 {payload.get('rating')} 评价数 {payload.get('review_count')} "
        f"分维度 {score.get('breakdown')}"
    )
    allowed = _extract_numerical_tokens(source_facts + " " + human_content)
    allowed_norm = {_norm_num(t) for t in allowed}
    present = _extract_numerical_tokens(text)
    fabricated = {t for t in present if _norm_num(t) not in allowed_norm}
    if fabricated:
        logger.warning(f"[Recommender] LLM 输出含编造数字 {fabricated}，回退模板")
        return {"llm_reason": fallback_reason, "llm_risks": fallback_risks}
    return {"llm_reason": text, "llm_risks": fallback_risks}


def recommend(limit: int = 10, platform: Optional[str] = None,
              min_score: float = 0.0, use_llm: bool = True) -> dict[str, Any]:
    """推荐列表（潜力分降序）"""
    sel_store = get_selection_store()
    urls, pool_latest = _pool()
    weights = sel_store.get_weights()
    items = []
    for url in urls:
        snap = next((s for s in pool_latest if s.get("url") == url), None)
        if platform and (snap or {}).get("platform") != platform:
            continue
        # LLM 理由后置：先过滤/排序/截断，仅对最终 top-N 调用 LLM（避免全量放大）
        item = _build_item(url, pool_latest, weights, use_llm=False)
        if item and item["score"]["total"] >= min_score:
            items.append(item)
    items.sort(key=lambda x: x["score"]["total"], reverse=True)
    items = items[:limit]
    if use_llm:
        for it in items:
            it.update(generate_reason(it))
    return {
        "items": items,
        "total": len(items),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def score_url(url: str, force_refresh: bool = False) -> Optional[dict[str, Any]]:
    """单品潜力评估（不带 LLM 理由，供前端潜力分列/单品页）"""
    _, pool_latest = _pool()
    weights = get_selection_store().get_weights()
    return _build_item(url, pool_latest, weights,
                       use_llm=False, force_refresh=force_refresh)


def batch_scores(urls: list[str]) -> dict[str, Any]:
    """批量读评分缓存（不触发重算；供监控表潜力分列）"""
    sel_store = get_selection_store()
    scores = {}
    for url in urls:
        cached = sel_store.get_score(url)
        if cached:
            scores[url] = cached["score_json"]
    return {"scores": scores,
            "generated_at": datetime.now().isoformat(timespec="seconds")}


def compare(urls: list[str]) -> dict[str, Any]:
    """多品对比：最新快照并排 + 差异字段列表"""
    store = get_store()
    items = []
    for url in urls:
        snap = store.latest_snapshot(url)
        watch = store.get_watch_by_url(url)
        items.append({
            "url": url,
            "name": (watch["name"] if watch else "") or (snap or {}).get("title") or url,
            "price": (snap or {}).get("price"),
            "original_price": (snap or {}).get("original_price"),
            "currency": (snap or {}).get("currency") or "CNY",
            "rating": (snap or {}).get("rating"),
            "review_count": (snap or {}).get("review_count"),
            "promo_text": (snap or {}).get("promo_text") or "",
            "in_stock": bool((snap or {}).get("in_stock")),
            "highlights": (snap or {}).get("highlights") or "",
            "crawled_at": (snap or {}).get("crawled_at"),
        })
    diff_fields = []
    for field in ("price", "rating", "review_count", "promo_text", "in_stock", "highlights"):
        if len({str(it.get(field)) for it in items}) > 1:
            diff_fields.append(field)
    return {"items": items, "diff_fields": diff_fields,
            "generated_at": datetime.now().isoformat(timespec="seconds")}


def generate_report(category: str = "", days: int = 30) -> str:
    """选品 Markdown 报告（同步返回，与 /competitor/scan 行为一致）

    category 为预留参数（当前版本不做品类过滤，Phase 2 榜单采集后启用）。
    """
    from backend.selection.trends import compute_trends

    rec = recommend(limit=10, use_llm=False)
    trends = compute_trends(get_store(), days=days)
    lines = [
        f"## 智能选品报告（{datetime.now().strftime('%Y-%m-%d %H:%M')}）",
        "",
        f"数据窗口: 最近 {days} 天，快照 {trends['sources']['snapshot_count']} 条",
        "",
        "### Top 推荐",
        "",
        "| 商品 | 平台 | 现价 | 评分 | 评价数 | 潜力分 |",
        "|---|---|---|---|---|---|",
    ]
    for it in rec["items"]:
        lines.append(
            f"| {it['title'][:30]} | {it['platform']} | "
            f"{it['latest_price'] if it['latest_price'] is not None else '-'} | "
            f"{it['rating'] if it['rating'] is not None else '-'} | "
            f"{it['review_count'] if it['review_count'] is not None else '-'} | "
            f"{it['score']['total']} |"
        )
    if trends["highlight_freq"]:
        top_kw = "、".join(h["keyword"] for h in trends["highlight_freq"][:8])
        lines.extend(["", "### 热卖卖点 Top8", "", top_kw])
    if trends["review_growth"]:
        lines.extend(["", "### 评价增速 Top3", ""])
        for g in trends["review_growth"][:3]:
            lines.append(f"- {g['name']}: 日增 {g['daily_delta']} 条")
    return "\n".join(lines)
