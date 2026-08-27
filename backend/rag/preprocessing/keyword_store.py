"""关键词规则动态管理 — SQLite 持久化 + 热加载。

替代 config/rag.py 中写死的 DEFAULT_KEYWORDS / SIGNAL_RULES。
config 中的值作为初始种子数据，首次启动自动导入。
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Any

from backend.config import DEFAULT_KEYWORDS, SIGNAL_RULES
from backend.shared.logger import logger

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS keyword_rules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword    TEXT NOT NULL,             -- 关键词
    doc_type   TEXT NOT NULL DEFAULT 'general', -- 归属文档类型（faq/product_spec/policy/compliance/legal/general）
    category   TEXT NOT NULL DEFAULT '',   -- 业务分类（商品管理/订单履约/...）
    weight     INTEGER NOT NULL DEFAULT 1, -- 权重（越高越重要）
    enabled    INTEGER NOT NULL DEFAULT 1, -- 1=启用, 0=禁用
    source     TEXT NOT NULL DEFAULT 'seed', -- seed=种子数据, manual=用户添加
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_kw_enabled ON keyword_rules(enabled);
CREATE INDEX IF NOT EXISTS idx_kw_doc_type ON keyword_rules(doc_type);
CREATE INDEX IF NOT EXISTS idx_kw_category ON keyword_rules(category);
"""


class KeywordRuleStore:
    """关键词规则持久化存储 — 线程安全。

    缓存策略: 读取时 60s 内命中缓存，超时从 DB 刷新。
    """
    _CACHE_TTL = 60  # 秒

    def __init__(self, db_path: str = "data/keyword_rules.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._cache: dict[str, Any] | None = None
        self._cache_ts: float = 0
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        conn = self._conn()
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        # 兼容旧表：doc_type 列不存在则追加
        try:
            conn.execute("SELECT doc_type FROM keyword_rules LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE keyword_rules ADD COLUMN doc_type TEXT NOT NULL DEFAULT 'general'")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kw_doc_type ON keyword_rules(doc_type)")
            conn.commit()
        # 种子数据导入（首次）
        count = conn.execute("SELECT COUNT(*) FROM keyword_rules").fetchone()[0]
        if count == 0:
            self._seed(conn)
        conn.close()

    # 种子数据 → doc_type 分配规则
    _SEED_DOC_TYPE_MAP = {
        "faq":         ["退货", "差评", "投诉", "faq", "售后", "保修", "索赔", "review", "FAQ", "常见问题", "售后流程", "物流时效", "退货政策", "退换", "客户", "买家", "好评", "Feedback", "AZ", "Chargeback"],
        "product_spec":["SKU", "SPU", "Listing", "上架", "下架", "变体", "品类", "类目", "品牌", "规格", "条码", "标题", "五点", "A+", "主图", "附图", "产品规格", "材质说明", "使用手册", "保养指南", "故障排查", "关键词策略", "搜索词", "排名", "BSR", "BestSeller"],
        "policy":      ["制度", "规范", "审批", "规定", "管理条例", "关键词", "否定词", "匹配类型", "归因", "预算", "广告政策", "投放规则", "竞价策略", "广告规范"],
        "compliance":  ["合规", "法规", "监管", "GDPR", "CCPA", "数据保护", "个人信息", "隐私政策"],
        "legal":       ["合同", "条款", "违约责任", "赔偿", "知识产权", "保密协议", "法律"],
    }

    def _seed(self, conn: sqlite3.Connection) -> None:
        """从 config 导入初始种子数据，按关键词分配 doc_type。"""
        # 构建反向索引: keyword → doc_type
        kw_to_doc: dict[str, str] = {}
        for doc_type, kws in self._SEED_DOC_TYPE_MAP.items():
            for kw in kws:
                kw_lower = kw.lower()
                if kw_lower not in kw_to_doc:
                    kw_to_doc[kw_lower] = doc_type

        rows: list[tuple] = []
        seen: set[str] = set()
        for kw in DEFAULT_KEYWORDS:
            w = kw.strip()
            if w.lower() in seen:
                continue
            seen.add(w.lower())
            dt = kw_to_doc.get(w.lower(), "general")
            rows.append((w, dt, "", 1, 1, "seed"))
        for cat, kws in SIGNAL_RULES.items():
            for kw in kws:
                w = kw.strip()
                if w.lower() in seen:
                    continue
                seen.add(w.lower())
                dt = kw_to_doc.get(w.lower(), "general")
                rows.append((w, dt, cat, 2, 1, "seed"))
        conn.executemany(
            "INSERT OR IGNORE INTO keyword_rules (keyword, doc_type, category, weight, enabled, source) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
        logger.info(f"[KeywordStore] 种子数据导入: {len(rows)} 条")

    # ── 查询（带缓存）──

    def _refresh_cache(self) -> dict:
        conn = self._conn()
        rows = conn.execute(
            "SELECT keyword, doc_type, category, weight FROM keyword_rules WHERE enabled=1 ORDER BY weight DESC"
        ).fetchall()
        conn.close()

        # 按 doc_type 分组（词串 + 带权重）
        by_doc_type: dict[str, list[str]] = {}
        by_doc_type_w: dict[str, list[tuple[str, int]]] = {}
        all_keywords: list[str] = []
        signal_rules: dict[str, list[str]] = {}
        for r in rows:
            kw = r["keyword"]
            dt = r["doc_type"]
            w = r["weight"]
            if dt not in by_doc_type:
                by_doc_type[dt] = []
                by_doc_type_w[dt] = []
            by_doc_type[dt].append(kw)
            by_doc_type_w[dt].append((kw, w))
            all_keywords.append(kw)
            cat = r["category"]
            if cat:
                if cat not in signal_rules:
                    signal_rules[cat] = []
                signal_rules[cat].append(kw)

        self._cache = {
            "keywords": all_keywords,
            "by_doc_type": by_doc_type,
            "by_doc_type_w": by_doc_type_w,
            "signal_rules": signal_rules,
        }
        self._cache_ts = time.time()
        return self._cache

    def get_rules_by_doc_type(self) -> dict[str, list[tuple[str, int]]]:
        """返回 {doc_type: [(keyword, weight), ...]}，60s 缓存。

        与 get_keywords_for_doc_type 不同：这里保留每条词的权重，
        供分类器 classify_with_confidence 按权重累加到类型得分。
        'general' 桶的通用词不返回（分类按类型归因，通用词不偏向任何类型）。
        """
        active = self.get_active()
        return active.get("by_doc_type_w", {})

    def get_active(self) -> dict:
        """返回 {"keywords": [...], "by_doc_type": {...}, "signal_rules": {...}}，60s 缓存"""
        now = time.time()
        if self._cache is not None and (now - self._cache_ts) < self._CACHE_TTL:
            return self._cache
        return self._refresh_cache()

    def get_keywords_for_doc_type(self, doc_type: str) -> list[str]:
        """只返回指定文档类型的关键词（用于按类型提取）"""
        active = self.get_active()
        by_dt = active.get("by_doc_type", {})
        general = by_dt.get("general", [])
        specific = by_dt.get(doc_type, [])
        return specific + general  # 通用词兜底

    # ── CRUD ──

    def list_all(self, doc_type: str = "", category: str = "", enabled: int | None = None, search: str = "") -> list[dict]:
        """列出所有规则（管理页用）"""
        conditions = []
        params: list[Any] = []
        if doc_type:
            conditions.append("doc_type = ?")
            params.append(doc_type)
        if category:
            conditions.append("category = ?")
            params.append(category)
        if enabled is not None:
            conditions.append("enabled = ?")
            params.append(enabled)
        if search:
            conditions.append("keyword LIKE ?")
            params.append(f"%{search}%")
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        conn = self._conn()
        rows = conn.execute(
            f"SELECT * FROM keyword_rules {where} ORDER BY doc_type, weight DESC, keyword",
            params,
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def list_doc_types(self) -> list[str]:
        """返回所有文档类型"""
        conn = self._conn()
        rows = conn.execute(
            "SELECT DISTINCT doc_type FROM keyword_rules ORDER BY doc_type"
        ).fetchall()
        conn.close()
        return [r["doc_type"] for r in rows]

    def list_categories(self) -> list[str]:
        """返回所有分类名"""
        conn = self._conn()
        rows = conn.execute(
            "SELECT DISTINCT category FROM keyword_rules WHERE category != '' ORDER BY category"
        ).fetchall()
        conn.close()
        return [r["category"] for r in rows]

    def upsert(self, keyword: str, doc_type: str = "general", category: str = "", weight: int = 1, enabled: int = 1) -> dict:
        """新增或更新"""
        with self._lock:
            conn = self._conn()
            existing = conn.execute(
                "SELECT id FROM keyword_rules WHERE keyword = ?", (keyword,)
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE keyword_rules SET doc_type=?, category=?, weight=?, enabled=?,
                       updated_at=datetime('now') WHERE id=?""",
                    (doc_type, category, weight, enabled, existing["id"]),
                )
            else:
                conn.execute(
                    """INSERT INTO keyword_rules (keyword, doc_type, category, weight, enabled, source, updated_at)
                       VALUES (?, ?, ?, ?, ?, 'manual', datetime('now'))""",
                    (keyword, doc_type, category, weight, enabled),
                )
            conn.commit()
            conn.close()
        self._cache = None  # 失效缓存
        return {"ok": True, "keyword": keyword}

    def batch_upsert(self, items: list[dict]) -> dict:
        """批量导入 [{keyword, doc_type?, category?, weight?}]"""
        for item in items:
            kw = item.get("keyword", "").strip()
            if not kw:
                continue
            self.upsert(kw, item.get("doc_type", "general"), item.get("category", ""),
                       item.get("weight", 1), item.get("enabled", 1))
        return {"ok": True, "added": len(items)}

    def delete(self, keyword: str) -> dict:
        with self._lock:
            conn = self._conn()
            conn.execute("DELETE FROM keyword_rules WHERE keyword = ?", (keyword,))
            conn.commit()
            conn.close()
        self._cache = None
        return {"ok": True}

    def toggle(self, keyword: str, enabled: int) -> dict:
        with self._lock:
            conn = self._conn()
            conn.execute(
                "UPDATE keyword_rules SET enabled=?, updated_at=datetime('now') WHERE keyword=?",
                (enabled, keyword),
            )
            conn.commit()
            conn.close()
        self._cache = None
        return {"ok": True, "enabled": bool(enabled)}


# 模块级单例
_store: KeywordRuleStore | None = None


def get_keyword_store(db_path: str = "data/keyword_rules.db") -> KeywordRuleStore:
    global _store
    if _store is None:
        _store = KeywordRuleStore(db_path)
    return _store
