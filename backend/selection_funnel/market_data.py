"""selection_funnel/market_data.py — 赛道数据上传通道：关键词榜 + 差评实证

原架构二期灰块（大盘趋势 / 拉词建池 / 竞品差评）的上传文件化落地
（2026-09-17 用户拍板：与选品导入通道同一形态，绕开数据源死穴）：

  1. 关键词榜（生意参谋/流量解析导出：关键词、搜索人气、点击率、转化率、竞争度）
     → keyword_stats 表 → 赛道画像（top 词 / 机会词 = 高搜索低竞争）→ 报告「赛道画像」段
  2. 差评实证（竞品工具导出评论：商品标题、评论内容、星级）
     → product_reviews 表 → 规则桶聚类 → 报告「痛点机会」段
     （竞品差评 = 产品改良机会，选品经典打法；P0 关键词桶，LLM 聚类 P1）

复用 import_pool 的解析基建（数值清洗/表头归一），同库 selection_import.db。
纪律：画像与痛点只富化报告，不做淘汰依据；候选-评论按标题包含匹配，
口径在报告披露。
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from typing import Any

from backend.selection_funnel.import_pool import IMPORT_DB_PATH, _norm_header, _to_float
from backend.shared.logger import logger

# 与商品导入池同库同 env（SELECTION_IMPORT_DB_PATH），三张表分工
MARKET_DB_PATH = IMPORT_DB_PATH

# ── 表头映射 ──────────────────────────────────────────────────────────
KEYWORD_ALIASES: dict[str, set[str]] = {
    "keyword": {"关键词", "搜索词", "核心关键词", "关键词名称", "keyword"},
    "search_pop": {"搜索人气", "搜索量", "搜索热度", "人气", "搜索指数", "search volume"},
    "click_rate": {"点击率", "点击率%", "ctr"},
    "pay_rate": {"支付转化率", "转化率", "支付转化率%", "cvr"},
    "competition": {"竞争度", "竞争强度", "在线商品数", "竞品数", "商品数"},
}
REVIEW_ALIASES: dict[str, set[str]] = {
    "product_title": {"商品标题", "商品", "宝贝标题", "标题", "商品名称", "product"},
    "content": {"评论内容", "评语", "内容", "评价", "评价内容", "comment", "review"},
    "star": {"星级", "评分", "打分", "star", "rating"},
}


def _map(headers: list[str], aliases: dict[str, set[str]]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    used: set[str] = set()
    for idx, h in enumerate(headers):
        nh = _norm_header(h)
        for field, names in aliases.items():
            if nh in names and field not in used:
                mapping[idx] = field
                used.add(field)
                break
    return mapping


def _rows(data: bytes | str) -> list[list[Any]]:
    """复用 import_pool.parse_table 的格式嗅探，但不过商品归一化——直接行矩阵。"""
    from backend.selection_funnel.import_pool import parse_table  # noqa: F401 (格式校验借用)
    import csv as _csv
    import io as _io
    from openpyxl import load_workbook
    if isinstance(data, bytes) and data[:2] == b"PK":
        wb = load_workbook(_io.BytesIO(data), data_only=True, read_only=True)
        return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)
                if any(c is not None for c in r)]
    if isinstance(data, bytes):
        data = data.decode("utf-8-sig", errors="replace")
    text = data
    rows = [r for r in _csv.reader(_io.StringIO(text),
                                   delimiter="\t" if text.splitlines() and
                                   text.splitlines()[0].count("\t") >= text.splitlines()[0].count(",")
                                   else ",")
            if any(c.strip() for c in r)]
    return rows


# ── 痛点桶（P0 规则聚类）─────────────────────────────────────────────
PAIN_BUCKETS: dict[str, tuple[str, ...]] = {
    "质量做工": ("质量", "劣质", "做工", "材质", "用料", "坏了", "破损", "断裂"),
    "物流包装": ("物流", "快递", "发货慢", "到货", "运输", "包装", "压坏"),
    "尺寸规格": ("尺寸", "大小", "偏大", "偏小", "规格", "克重", "分量"),
    "气味口感": ("味道", "异味", "臭", "气味", "香精", "口感"),
    "描述不符": ("色差", "不符", "图片", "实物", "与描述", "虚假"),
    "售后服务": ("客服", "售后", "态度", "退款难", "推诿"),
}
NEGATIVE_STAR_MAX = 3.0   # 星级 ≤3 视为差评；无星级列时全量计


# ── 存储 ──────────────────────────────────────────────────────────────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS keyword_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT, category TEXT DEFAULT '',
    keyword TEXT, search_pop REAL, click_rate REAL, pay_rate REAL,
    competition REAL, imported_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_kw_cat ON keyword_stats(category);
CREATE TABLE IF NOT EXISTS product_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT, category TEXT DEFAULT '',
    product_title TEXT, content TEXT, star REAL, imported_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_rv_cat ON product_reviews(category);
"""


class MarketStore:
    def __init__(self, db_path: str = MARKET_DB_PATH):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def add_keywords(self, rows: list[dict], category: str) -> tuple[str, int]:
        batch_id = f"kw-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO keyword_stats (batch_id, category, keyword, search_pop,"
                " click_rate, pay_rate, competition, imported_at) VALUES (?,?,?,?,?,?,?,?)",
                [(batch_id, category, r.get("keyword") or "", r.get("search_pop"),
                  r.get("click_rate"), r.get("pay_rate"), r.get("competition"), now)
                 for r in rows])
        return batch_id, len(rows)

    def add_reviews(self, rows: list[dict], category: str) -> tuple[str, int]:
        batch_id = f"rv-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO product_reviews (batch_id, category, product_title,"
                " content, star, imported_at) VALUES (?,?,?,?,?,?)",
                [(batch_id, category, r.get("product_title") or "",
                  r.get("content") or "", r.get("star"), now) for r in rows])
        return batch_id, len(rows)

    def keywords(self, category: str = "") -> list[dict]:
        sql = "SELECT keyword, search_pop, click_rate, pay_rate, competition FROM keyword_stats"
        params: tuple = ()
        if category:
            sql += " WHERE category LIKE ?"
            params = (f"%{category}%",)
        sql += " ORDER BY search_pop DESC"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def reviews(self, category: str = "") -> list[dict]:
        sql = "SELECT product_title, content, star FROM product_reviews"
        params: tuple = ()
        if category:
            sql += " WHERE category LIKE ?"
            params = (f"%{category}%",)
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def clear_batch(self, batch_id: str) -> int:
        """按批次清除关键词/差评（2026-09-17 全流程实测补：页面按批次清除此前只覆盖商品表）。"""
        removed = 0
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM keyword_stats WHERE batch_id = ?", (batch_id,))
            removed += cur.rowcount
            cur = conn.execute("DELETE FROM product_reviews WHERE batch_id = ?", (batch_id,))
            removed += cur.rowcount
        return removed


_default_market: MarketStore | None = None


def get_market_store() -> MarketStore:
    global _default_market
    if _default_market is None:
        _default_market = MarketStore()
    return _default_market


# ── 导入一条龙 ────────────────────────────────────────────────────────
def _parse_generic(data: bytes | str, aliases: dict[str, set[str]],
                   required: str) -> list[dict]:
    rows = _rows(data)
    if not rows:
        raise ValueError("导入表格为空")
    headers = [str(h or "").strip() for h in rows[0]]
    mapping = _map(headers, aliases)
    if required not in mapping.values():
        raise ValueError(f"表头中未识别到「{required}」列")
    out = []
    for row in rows[1:]:
        item: dict[str, Any] = {}
        for idx, cell in enumerate(row):
            field = mapping.get(idx)
            if not field:
                continue
            if field in ("keyword", "product_title", "content"):
                item[field] = str(cell or "").strip()
            else:
                item[field] = _to_float(cell)
        if item.get(required):
            out.append(item)
    if not out:
        raise ValueError("导入表格中没有有效数据行")
    return out


def import_keywords(data: bytes | str, category: str = "") -> tuple[str, int, list[str]]:
    try:
        rows = _parse_generic(data, KEYWORD_ALIASES, "keyword")
    except Exception as e:
        logger.warning("[MarketData] 关键词榜解析失败: %s", e)
        return "", 0, [f"导入失败: {e}"]
    batch_id, n = get_market_store().add_keywords(rows, category=category)
    return batch_id, n, [f"关键词榜批次 {batch_id}：{n} 个词已入库"]


def import_reviews(data: bytes | str, category: str = "") -> tuple[str, int, list[str]]:
    try:
        rows = _parse_generic(data, REVIEW_ALIASES, "content")
    except Exception as e:
        logger.warning("[MarketData] 差评解析失败: %s", e)
        return "", 0, [f"导入失败: {e}"]
    batch_id, n = get_market_store().add_reviews(rows, category=category)
    return batch_id, n, [f"差评批次 {batch_id}：{n} 条已入库"]


# ── 赛道画像 ──────────────────────────────────────────────────────────
def market_snapshot(category: str) -> dict:
    """赛道画像：top 词 + 机会词（搜索人气高、竞争度低）。

    机会词口径：人气名次 − 竞争名次差值最大的词（两列都有值才参与）。
    """
    kws = get_market_store().keywords(category)
    if not kws:
        return {}
    top = kws[:10]
    scored = [k for k in kws if k.get("search_pop") is not None
              and k.get("competition") is not None]
    opportunities: list[dict] = []
    if len(scored) >= 3:
        by_pop = {k["keyword"]: i for i, k in enumerate(
            sorted(scored, key=lambda x: -(x["search_pop"] or 0)))}
        by_comp = {k["keyword"]: i for i, k in enumerate(
            sorted(scored, key=lambda x: (x["competition"] or 0)))}
        opportunities = sorted(
            scored, key=lambda k: by_pop.get(k["keyword"], 0) - by_comp.get(k["keyword"], 0),
            reverse=True)[:5]
    return {"total": len(kws), "top": top, "opportunities": opportunities}


# ── 竞争格局（商品榜结构化，2026-09-17 六步法补缺）─────────────────────
# 品牌桶：标题命中这些词视作品牌款（结构性标记；类目品牌词用 env 扩充，
# 绝不把具体品牌名写死在业务代码里 —— 与阈值集中配置同一纪律）
_BRAND_WORDS_DEFAULT: tuple[str, ...] = ("旗舰店", "官方", "专卖", "直营")
BRAND_WORDS: tuple[str, ...] = (
    tuple(w.strip() for w in
          os.getenv("SELECTION_FUNNEL_BRAND_WORDS", "").split(",") if w.strip())
    or _BRAND_WORDS_DEFAULT
)
REVIEW_RATIO_HIGH = 0.5    # 评论数/销量 > 0.5：刷评或销量口径失真嫌疑
REVIEW_RATIO_LOW = 0.02    # 评论数/销量 < 0.02 且销量达线：销量数据存疑
REVIEW_RATIO_LOW_MIN_SALES = 1000


def _percentile(sorted_vals: list[float], q: float) -> float | None:
    """线性插值分位（numpy 默认口径）；空列表返回 None。"""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return round(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac, 2)


def competition_structure(products: list[dict]) -> dict:
    """商品榜竞争结构：CR5 / 价格分位 / 评价比异常 / 品牌占比。

    只做结构描述富化报告，不做淘汰依据（与赛道画像/痛点同一纪律）。
    传建池全量快照（pool）而非筛后候选 —— 竞争格局要看全池。

    Returns:
        {total, cr5, price_p25, price_p50, price_p75, anomalies,
         brand_like, brand_ratio, brand_words_hit, notes}；空池返回 {}。
    """
    if not products:
        return {}
    notes: list[str] = []
    prices = sorted(float(p["price"]) for p in products if p.get("price"))

    # CR5：热度前 5 款占全池热度份额（评论数代销量，缺评论数用 sales 兜底）
    heat = [(p.get("review_count") or 0) or (p.get("sales") or 0) for p in products]
    cr5: float | None = None
    if sum(heat) > 0:
        top5 = sorted(heat, reverse=True)[:5]
        cr5 = round(sum(top5) / sum(heat), 4)
        if len([h for h in heat if h]) < 5:
            notes.append("有效热度数据不足 5 款，CR5 仅作参考")
    else:
        notes.append("全池缺评论数/销量，无法计算 CR5")

    # 评价比异常（评论数/销量 双字段齐才判）
    anomalies: list[dict] = []
    for p in products:
        rc, sales = p.get("review_count"), p.get("sales")
        if not rc or not sales:
            continue
        ratio = rc / sales
        title = (p.get("title") or "")[:24]
        if ratio > REVIEW_RATIO_HIGH:
            anomalies.append({"title": title, "ratio": round(ratio, 3),
                              "flag": "评论数接近销量（刷评或销量口径失真）"})
        elif ratio < REVIEW_RATIO_LOW and sales >= REVIEW_RATIO_LOW_MIN_SALES:
            anomalies.append({"title": title, "ratio": round(ratio, 3),
                              "flag": "销量高评论极少（评价关闭或销量存疑）"})

    # 品牌桶
    brand_words_hit: dict[str, int] = {}
    brand_like = 0
    for p in products:
        title = p.get("title") or ""
        hit = [w for w in BRAND_WORDS if w in title]
        if hit:
            brand_like += 1
            for w in hit:
                brand_words_hit[w] = brand_words_hit.get(w, 0) + 1

    return {
        "total": len(products),
        "cr5": cr5,
        "price_p25": _percentile(prices, 0.25),
        "price_p50": _percentile(prices, 0.50),
        "price_p75": _percentile(prices, 0.75),
        "anomalies": anomalies[:5],
        "brand_like": brand_like,
        "brand_ratio": round(brand_like / len(products), 3),
        "brand_words_hit": brand_words_hit,
        "notes": notes,
    }


# ── 差评痛点 ──────────────────────────────────────────────────────────
def _match_title(product_title: str, candidate_title: str) -> bool:
    """标题包含匹配（双向、去空白），P0 口径；报告披露。"""
    a, b = _norm_header(product_title), _norm_header(candidate_title)
    if len(a) < 4 or len(b) < 4:
        return False
    return a in b or b in a


def pain_points_for(titles: list[str], category: str,
                    top_n_pains: int = 3) -> list[dict]:
    """按候选标题关联差评，规则桶聚类出 top 痛点。"""
    reviews = get_market_store().reviews(category)
    if not reviews:
        return []
    out: list[dict] = []
    for title in titles:
        matched = [r for r in reviews if _match_title(r.get("product_title") or "", title)]
        negatives = [r for r in matched
                     if r.get("star") is None or (r.get("star") or 5) <= NEGATIVE_STAR_MAX]
        if not negatives:
            continue
        bucket_count: dict[str, int] = {}
        for r in negatives:
            content = r.get("content") or ""
            for bucket, words in PAIN_BUCKETS.items():
                if any(w in content for w in words):
                    bucket_count[bucket] = bucket_count.get(bucket, 0) + 1
        top_pains = sorted(bucket_count.items(), key=lambda kv: -kv[1])[:top_n_pains]
        out.append({"title": title[:28], "review_total": len(matched),
                    "negative": len(negatives), "pains": top_pains})
    return out
